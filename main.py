import os
import requests
import json
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from flask import Flask, jsonify, request
import logging
import re
from html import unescape

# 로깅 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# 환경 변수
BOT_TOKEN = os.environ.get('BOT_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')

# 한국 시간대 설정
KST = timezone(timedelta(hours=9))
EST = timezone(timedelta(hours=-5))  # SEC는 미국 동부시간 기준

def get_korean_time():
    """현재 한국 시간 반환"""
    return datetime.now(KST)

def get_yesterday_date():
    """어제 날짜 (미국 동부시간 기준)"""
    est_now = datetime.now(EST)
    yesterday = est_now - timedelta(days=1)
    return yesterday.strftime("%Y-%m-%d")

def send_telegram_message(message):
    """텔레그램 메시지 전송"""
    if not BOT_TOKEN or not CHAT_ID:
        error_msg = f"환경변수 누락"
        logger.error(error_msg)
        return {"status": "error", "message": error_msg}
    
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    
    payload = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": False
    }
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        response_data = response.json()
        
        if response.status_code == 200 and response_data.get('ok'):
            logger.info("✅ 텔레그램 전송 성공")
            return {"status": "success"}
        else:
            error_msg = response_data.get('description', '알 수 없는 오류')
            logger.error(f"❌ 텔레그램 오류: {error_msg}")
            return {"status": "error", "message": error_msg}
            
    except Exception as e:
        logger.error(f"❌ 오류: {str(e)}")
        return {"status": "error", "message": str(e)}

def get_all_recent_filings():
    """모든 최근 Filing 가져오기 (Form 타입 무관)"""
    all_filings = []
    
    try:
        # 전체 최근 Filing RSS (최대 100개)
        base_url = "https://www.sec.gov/cgi-bin/browse-edgar"
        
        # 여러 페이지 시도
        for start in [0, 100, 200]:
            params = {
                'action': 'getcurrent',
                'owner': 'exclude',
                'start': start,
                'count': 100,
                'output': 'atom'
            }
            
            headers = {
                'User-Agent': 'SEC ETF Monitor/1.0 (monitor@example.com)',
                'Accept': 'application/atom+xml,application/xml,text/xml'
            }
            
            response = requests.get(base_url, params=params, headers=headers, timeout=20)
            logger.info(f"RSS 요청 (start={start}): 상태코드 {response.status_code}")
            
            if response.status_code == 200:
                filings = parse_general_rss(response.content)
                all_filings.extend(filings)
                logger.info(f"페이지 {start//100+1}: {len(filings)}개 ETF Filing 발견")
    
    except Exception as e:
        logger.error(f"RSS 오류: {str(e)}")
    
    # 중복 제거
    unique_filings = []
    seen_urls = set()
    
    for filing in all_filings:
        if filing['url'] not in seen_urls:
            seen_urls.add(filing['url'])
            unique_filings.append(filing)
    
    # 날짜순 정렬
    unique_filings.sort(key=lambda x: x['filing_date'], reverse=True)
    
    return unique_filings

def parse_general_rss(content):
    """일반 RSS 피드 파싱 (ETF만 필터)"""
    filings = []
    
    try:
        # XML 파싱
        root = ET.fromstring(content)
        
        # Namespace 처리
        ns = {}
        if root.tag.startswith('{'):
            ns = {'atom': root.tag[1:root.tag.index('}')]}
        
        # entry 또는 item 찾기
        entries = root.findall('.//atom:entry', ns) if ns else root.findall('.//entry')
        if not entries:
            entries = root.findall('.//item')  # RSS 2.0 형식
        
        logger.info(f"총 {len(entries)}개 엔트리 발견")
        
        yesterday = get_yesterday_date()
        today = datetime.now(EST).strftime("%Y-%m-%d")
        valid_dates = [yesterday, today]
        
        for entry in entries:
            try:
                # 제목, 링크, 날짜 추출 (다양한 형식 지원)
                title = None
                link = None
                date = None
                summary = None
                
                # Atom 형식
                if ns:
                    title_elem = entry.find('atom:title', ns)
                    link_elem = entry.find('atom:link', ns)
                    date_elem = entry.find('atom:updated', ns) or entry.find('atom:published', ns)
                    summary_elem = entry.find('atom:summary', ns)
                else:
                    title_elem = entry.find('title')
                    link_elem = entry.find('link')
                    date_elem = entry.find('updated') or entry.find('published') or entry.find('pubDate')
                    summary_elem = entry.find('summary') or entry.find('description')
                
                # 값 추출
                if title_elem is not None:
                    title = title_elem.text or ""
                if link_elem is not None:
                    link = link_elem.get('href') if link_elem.get('href') else link_elem.text
                if date_elem is not None:
                    date_text = date_elem.text or ""
                    date = date_text[:10] if len(date_text) >= 10 else ""
                if summary_elem is not None:
                    summary = summary_elem.text or ""
                
                # 필수 필드 체크
                if not title or not link:
                    continue
                
                # 날짜 필터
                if date and date not in valid_dates:
                    continue
                
                # HTML 엔티티 디코드
                title = unescape(title)
                if summary:
                    summary = unescape(summary)
                
                # ETF 관련 Filing인지 확인 (더 넓은 범위로)
                combined_text = title.lower()
                if summary:
                    combined_text += " " + summary.lower()
                
                # ETF 판별 기준 (느슨하게)
                is_etf = False
                
                # 1. ETF 키워드
                etf_keywords = ['etf', 'exchange-traded', 'exchange traded']
                if any(kw in combined_text for kw in etf_keywords):
                    is_etf = True
                
                # 2. ETF 관련 Form 타입
                etf_forms = ['n-1a', '485apos', '485bxt', '497', 'n-8a', 'n-8b']
                if any(form in combined_text for form in etf_forms):
                    is_etf = True
                
                # 3. 주요 ETF 운용사
                etf_companies = ['ishares', 'spdr', 'vanguard', 'invesco', 'proshares', 
                               'vaneck', 'ark invest', 'wisdomtree', 'first trust', 
                               'global x', 'tuttle', 'simplify', 'roundhill']
                if any(company in combined_text for company in etf_companies):
                    is_etf = True
                
                if not is_etf:
                    continue
                
                # 제외 키워드 (최소화)
                exclude = ['withdrawal', 'termination', 'liquidation', 'delisting']
                if any(kw in combined_text for kw in exclude):
                    continue
                
                # Form 타입 추출
                form_type = extract_form_type(title)
                if not form_type:
                    form_type = "ETF Filing"
                
                # ETF 이름 추출
                etf_name = extract_etf_name_simple(title, summary)
                
                if etf_name:
                    filing = {
                        "etf_name": etf_name,
                        "filing_type": form_type,
                        "filing_date": date or yesterday,
                        "url": link
                    }
                    filings.append(filing)
                    logger.info(f"✅ ETF 발견: {etf_name[:50]}...")
                
            except Exception as e:
                logger.error(f"엔트리 파싱 오류: {str(e)[:100]}")
                continue
        
    except Exception as e:
        logger.error(f"XML 파싱 오류: {str(e)}")
    
    return filings

def extract_form_type(title):
    """Form 타입 추출"""
    # Form 패턴
    patterns = [
        r'\b(N-1A/?A?)\b',
        r'\b(485APOS)\b',
        r'\b(485BXT)\b',
        r'\b(497K?)\b',
        r'\b(N-8[AB](?:-2)?)\b',
        r'Form\s+([\w-]+)'
    ]
    
    for pattern in patterns:
        match = re.search(pattern, title, re.IGNORECASE)
        if match:
            return match.group(1).upper()
    
    return None

def extract_etf_name_simple(title, summary):
    """ETF 이름 추출 (심플 버전)"""
    # CIK, Filer 등 제거
    clean_text = re.sub(r'\(\d{10}\)', '', title)
    clean_text = re.sub(r'\(Filer\)', '', clean_text)
    clean_text = re.sub(r'\(Subject\)', '', clean_text)
    
    # Form 타입 제거
    clean_text = re.sub(r'Form\s+[\w/-]+', '', clean_text, flags=re.IGNORECASE)
    clean_text = re.sub(r'\b(?:N-1A|485APOS|485BXT|497K?|N-8[AB](?:-2)?)\b', '', clean_text, flags=re.IGNORECASE)
    
    # 회사명과 ETF 이름 분리 시도
    parts = re.split(r'\s*[-–—]\s*', clean_text)
    
    # ETF 이름 찾기
    etf_name = None
    
    for part in parts:
        if 'etf' in part.lower():
            # ETF가 포함된 부분 사용
            etf_name = part.strip()
            break
    
    # 못 찾았으면 전체에서 ETF 패턴 찾기
    if not etf_name:
        match = re.search(r'([A-Z][A-Za-z0-9\s&\-\.]+\s+(?:ETF|Fund|Trust))', clean_text)
        if match:
            etf_name = match.group(1).strip()
    
    # 그래도 없으면 첫 번째 의미있는 부분
    if not etf_name and parts:
        for part in parts:
            part = part.strip()
            if len(part) > 10 and not part.startswith('('):
                etf_name = part
                break
    
    # 정리
    if etf_name:
        etf_name = re.sub(r'\s+', ' ', etf_name).strip()
        etf_name = etf_name[:100]  # 최대 길이
        
        # 너무 짧거나 의미없으면 제외
        if len(etf_name) < 5:
            return None
            
        return etf_name
    
    return None

def format_etf_report(filings):
    """ETF 리포트 포맷"""
    korean_time = get_korean_time()
    yesterday = get_yesterday_date()
    
    report = f"""📊 <b>SEC ETF 신규 상장신청</b>
━━━━━━━━━━━━━━━━━
📅 {yesterday} (미국) | {korean_time.strftime('%H:%M')} KST

"""
    
    # 어제 날짜 Filing만
    yesterday_filings = [f for f in filings if f['filing_date'] == yesterday]
    
    if not yesterday_filings:
        report += """⚠️ 신규 상장신청 없음

어제는 ETF 신규 상장신청이 없었습니다."""
    else:
        report += f"""🆕 <b>신규 {len(yesterday_filings)}건</b>

"""
        for filing in yesterday_filings:
            # ETF 이름 표시 (최대 60자)
            display_name = filing['etf_name']
            if len(display_name) > 60:
                display_name = display_name[:57] + "..."
            
            report += f"""• <b>{display_name}</b>
  {filing['filing_type']} | <a href="{filing['url']}">SEC Filing →</a>

"""
    
    # 디버깅 정보 (오늘 것도 있으면)
    today_filings = [f for f in filings if f['filing_date'] == datetime.now(EST).strftime("%Y-%m-%d")]
    if today_filings:
        report += f"\n💡 오늘 추가: {len(today_filings)}건"
    
    return report

@app.route('/')
def home():
    """헬스 체크"""
    return jsonify({
        "status": "healthy",
        "service": "SEC ETF Bot",
        "time": get_korean_time().isoformat()
    })

@app.route('/etf-report')
def send_etf_report():
    """SEC ETF 리포트 발송"""
    try:
        logger.info("="*50)
        logger.info(f"ETF 리포트 생성 시작 - {get_korean_time()}")
        
        # 모든 최근 Filing에서 ETF 찾기
        filings = get_all_recent_filings()
        logger.info(f"총 {len(filings)}개 ETF Filing 발견")
        
        # 리포트 생성
        report = format_etf_report(filings)
        
        # 텔레그램 전송
        result = send_telegram_message(report)
        
        # 상세 정보 반환
        yesterday = get_yesterday_date()
        yesterday_count = len([f for f in filings if f['filing_date'] == yesterday])
        
        return jsonify({
            "status": "success",
            "total_filings": len(filings),
            "yesterday_count": yesterday_count,
            "sample_filings": filings[:5],
            "timestamp": get_korean_time().isoformat()
        })
        
    except Exception as e:
        logger.error(f"오류: {str(e)}")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

@app.route('/test-sec-data')
def test_sec_data():
    """SEC 데이터 테스트"""
    try:
        filings = get_all_recent_filings()
        
        # 날짜별 집계
        by_date = {}
        for filing in filings:
            date = filing['filing_date']
            if date not in by_date:
                by_date[date] = []
            by_date[date].append(filing)
        
        return jsonify({
            "status": "success",
            "total": len(filings),
            "by_date_count": {date: len(items) for date, items in by_date.items()},
            "all_filings": filings,
            "yesterday": get_yesterday_date(),
            "timestamp": get_korean_time().isoformat()
        })
        
    except Exception as e:
        return jsonify({
            "status": "error", 
            "message": str(e)
        }), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
