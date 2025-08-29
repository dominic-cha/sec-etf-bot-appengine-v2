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

# 신규 ETF 상장신청 Form 타입만
NEW_ETF_FORMS = ['N-1A', '485APOS', 'N-8A']  # 485BXT 제외!

# 제외할 Form 타입
EXCLUDE_FORMS = ['485BXT', '497K', 'N-1A/A', 'POS AM', 'POSASR']

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
    """모든 최근 Filing 가져오기 (신규 ETF만)"""
    all_filings = []
    
    try:
        # 전체 최근 Filing RSS
        base_url = "https://www.sec.gov/cgi-bin/browse-edgar"
        
        # 여러 페이지 확인
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
            
            if response.status_code == 200:
                filings = parse_general_rss(response.content)
                all_filings.extend(filings)
                logger.info(f"페이지 {start//100+1}: {len(filings)}개 신규 ETF Filing 발견")
    
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

def is_new_etf_filing(title, summary, form_type):
    """신규 ETF 상장신청인지 확인"""
    combined = (title + " " + (summary or "")).lower()
    
    # 1. 제외할 Form 타입 체크
    for exclude_form in EXCLUDE_FORMS:
        if exclude_form.lower() in combined:
            logger.debug(f"제외: {exclude_form} 발견")
            return False
    
    # 2. Amendment 제외
    if '/a' in combined or 'amendment' in combined:
        # 단, 485APOS는 Amendment가 아님
        if '485apos' not in combined:
            logger.debug("제외: Amendment")
            return False
    
    # 3. Post-Effective Amendment 제외
    if 'post-effective' in combined or 'post effective' in combined:
        logger.debug("제외: Post-Effective Amendment")
        return False
    
    # 4. 제외 키워드
    exclude_keywords = [
        'withdrawal', 'termination', 'liquidation', 'delisting',
        'merger', 'supplement', 'updates', 'modification'
    ]
    
    for keyword in exclude_keywords:
        if keyword in combined:
            logger.debug(f"제외: {keyword}")
            return False
    
    # 5. 신규 ETF Form 타입 확인
    for new_form in NEW_ETF_FORMS:
        if new_form.lower() in combined:
            logger.debug(f"신규 ETF: {new_form} 확인")
            return True
    
    # 6. 신규 ETF 키워드 확인
    new_etf_keywords = [
        'initial registration', 'new etf', 'new exchange-traded',
        'registration statement', 'form n-1a', 'form 485apos'
    ]
    
    for keyword in new_etf_keywords:
        if keyword in combined:
            logger.debug(f"신규 ETF 키워드: {keyword}")
            return True
    
    return False

def parse_general_rss(content):
    """RSS 피드 파싱 (신규 ETF만)"""
    filings = []
    
    try:
        # XML 파싱
        root = ET.fromstring(content)
        
        # Namespace 처리
        ns = {}
        if root.tag.startswith('{'):
            ns = {'atom': root.tag[1:root.tag.index('}')]}
        
        # entry 찾기
        entries = root.findall('.//atom:entry', ns) if ns else root.findall('.//entry')
        if not entries:
            entries = root.findall('.//item')
        
        logger.info(f"총 {len(entries)}개 엔트리 검토")
        
        yesterday = get_yesterday_date()
        today = datetime.now(EST).strftime("%Y-%m-%d")
        valid_dates = [yesterday, today]
        
        for entry in entries:
            try:
                # 기본 정보 추출
                title = None
                link = None
                date = None
                summary = None
                
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
                    title = unescape(title_elem.text or "")
                if link_elem is not None:
                    link = link_elem.get('href') if link_elem.get('href') else link_elem.text
                if date_elem is not None:
                    date_text = date_elem.text or ""
                    date = date_text[:10] if len(date_text) >= 10 else ""
                if summary_elem is not None:
                    summary = unescape(summary_elem.text or "")
                
                # 필수 필드 체크
                if not title or not link:
                    continue
                
                # 날짜 필터
                if date and date not in valid_dates:
                    continue
                
                # ETF 여부 확인
                combined_text = title.lower()
                if summary:
                    combined_text += " " + summary.lower()
                
                # ETF가 아니면 스킵
                if 'etf' not in combined_text and 'exchange-traded' not in combined_text:
                    continue
                
                # Form 타입 추출
                form_type = extract_form_type(title)
                
                # 신규 ETF 상장신청인지 확인
                if not is_new_etf_filing(title, summary, form_type):
                    continue
                
                # ETF 이름 추출
                etf_name = extract_etf_name_clean(title, summary)
                
                if etf_name:
                    filing = {
                        "etf_name": etf_name,
                        "filing_type": form_type or "ETF Filing",
                        "filing_date": date or yesterday,
                        "url": link
                    }
                    filings.append(filing)
                    logger.info(f"✅ 신규 ETF: {etf_name} ({form_type})")
                
            except Exception as e:
                continue
        
    except Exception as e:
        logger.error(f"XML 파싱 오류: {str(e)}")
    
    return filings

def extract_form_type(title):
    """Form 타입 추출"""
    # Form 패턴 (485BXT 제외)
    patterns = [
        r'\b(N-1A)\b',  # /A 없는 것만
        r'\b(485APOS)\b',
        r'\b(N-8A)\b',
        r'\b(497)\b',  # K 없는 것만
        r'Form\s+(N-1A|485APOS|N-8A|497)\b'
    ]
    
    for pattern in patterns:
        match = re.search(pattern, title, re.IGNORECASE)
        if match:
            form = match.group(1).upper()
            # 485BXT는 제외
            if form != '485BXT':
                return form
    
    return None

def extract_etf_name_clean(title, summary):
    """ETF 이름 추출 (개선)"""
    # CIK, Filer 등 제거
    clean_text = re.sub(r'\(\d{10}\)', '', title)
    clean_text = re.sub(r'\(Filer\)', '', clean_text)
    clean_text = re.sub(r'\(Subject\)', '', clean_text)
    
    # Form 타입 제거
    clean_text = re.sub(r'Form\s+[\w/-]+', '', clean_text, flags=re.IGNORECASE)
    clean_text = re.sub(r'\b(?:N-1A|485APOS|485BXT|497K?|N-8[AB])\b', '', clean_text, flags=re.IGNORECASE)
    
    # Summary에서 ETF 이름 우선 추출
    if summary:
        # Series Name 패턴
        series_match = re.search(r'(?:Series Name|Fund Name)[:\s]*([^,\n]+(?:ETF|Exchange[- ]Traded Fund?))', 
                                summary, re.IGNORECASE)
        if series_match:
            name = series_match.group(1).strip()
            name = re.sub(r'\s+', ' ', name)
            if len(name) > 5:
                return name
    
    # Title에서 ETF 이름 찾기
    parts = re.split(r'\s*[-–—]\s*', clean_text)
    
    for part in parts:
        if 'etf' in part.lower():
            part = part.strip()
            part = re.sub(r'\s+', ' ', part)
            if len(part) > 5:
                return part
    
    # ETF 패턴 매칭
    match = re.search(r'([A-Z][A-Za-z0-9\s&\-\.]+\s+ETF)', clean_text)
    if match:
        name = match.group(1).strip()
        if len(name) > 5:
            return name
    
    return None

def format_etf_report(filings):
    """ETF 리포트 포맷"""
    korean_time = get_korean_time()
    yesterday = get_yesterday_date()
    
    report = f"""<b>SEC ETF 신규 상장신청</b>
─────────────────────
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
            report += f"""  • <b>{filing['etf_name']}</b>
    {filing['filing_type']} | <a href="{filing['url']}">SEC Filing →</a>

"""
    
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
        
        # 신규 ETF Filing만 수집
        filings = get_all_recent_filings()
        logger.info(f"총 {len(filings)}개 신규 ETF Filing 발견")
        
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
        
        # Form 타입별 집계
        by_form = {}
        for filing in filings:
            form = filing['filing_type']
            if form not in by_form:
                by_form[form] = 0
            by_form[form] += 1
        
        return jsonify({
            "status": "success",
            "total": len(filings),
            "by_date_count": {date: len(items) for date, items in by_date.items()},
            "by_form_count": by_form,
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
