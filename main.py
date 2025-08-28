import os
import requests
import json
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from flask import Flask, jsonify, request
import logging
import re
from html import unescape
import hashlib
import time

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

# 중복 실행 방지용
last_execution = {}

def get_korean_time():
    """현재 한국 시간 반환"""
    return datetime.now(KST)

def get_date_range():
    """조회 날짜 범위 (최근 3일)"""
    est_now = datetime.now(EST)
    dates = []
    for i in range(3):  # 오늘 포함 3일간
        date = est_now - timedelta(days=i)
        dates.append(date.strftime("%Y-%m-%d"))
    return dates

def prevent_duplicate_execution():
    """중복 실행 방지 (5초 이내 재실행 차단)"""
    global last_execution
    current_time = time.time()
    endpoint = request.endpoint
    
    if endpoint in last_execution:
        if current_time - last_execution[endpoint] < 5:
            logger.warning(f"중복 실행 방지: {endpoint}")
            return True
    
    last_execution[endpoint] = current_time
    return False

def send_telegram_message(message):
    """텔레그램 메시지 전송"""
    if not BOT_TOKEN or not CHAT_ID:
        error_msg = f"환경변수 누락 - BOT_TOKEN: {bool(BOT_TOKEN)}, CHAT_ID: {bool(CHAT_ID)}"
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
            logger.info("✅ 텔레그램 메시지 전송 성공")
            return {"status": "success", "message": "텔레그램 전송 성공"}
        else:
            error_msg = response_data.get('description', '알 수 없는 오류')
            logger.error(f"❌ 텔레그램 API 오류: {error_msg}")
            return {"status": "error", "message": f"텔레그램 API 오류: {error_msg}"}
            
    except Exception as e:
        logger.error(f"❌ 예상치 못한 오류: {str(e)}")
        return {"status": "error", "message": f"예상치 못한 오류: {str(e)}"}

def get_all_etf_filings():
    """SEC에서 최근 모든 ETF 관련 Filing 가져오기 (최근 100개 검토)"""
    all_filings = []
    
    # 다양한 Form 타입 확인
    form_types = ["N-1A", "485APOS", "485BXT", "497", "N-8A", "N-8B-2"]
    
    for form_type in form_types:
        filings = get_filings_by_form(form_type)
        if filings:
            all_filings.extend(filings)
            logger.info(f"{form_type}: {len(filings)}개 발견")
    
    # 전체 최근 Filing도 확인 (Form 타입 관계없이)
    general_filings = get_recent_filings()
    all_filings.extend(general_filings)
    
    # 중복 제거
    unique_filings = []
    seen = set()
    for filing in all_filings:
        # URL과 날짜로 유니크 키 생성
        key = f"{filing['url']}_{filing['filing_date']}"
        if key not in seen:
            seen.add(key)
            unique_filings.append(filing)
    
    # 날짜순 정렬
    unique_filings.sort(key=lambda x: x['filing_date'], reverse=True)
    
    logger.info(f"총 {len(unique_filings)}개 유니크한 Filing 수집")
    return unique_filings

def get_recent_filings():
    """최근 전체 Filing에서 ETF 찾기"""
    try:
        # 전체 최근 Filing RSS
        rss_url = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&output=atom"
        
        headers = {
            'User-Agent': 'SEC ETF Monitor Bot/1.0 (Contact: monitor@example.com)',
            'Accept': 'application/atom+xml,application/xml'
        }
        
        response = requests.get(rss_url, headers=headers, timeout=15)
        
        if response.status_code != 200:
            return []
        
        filings = parse_rss_feed(response.content, "GENERAL")
        return filings
        
    except Exception as e:
        logger.error(f"Recent filings 오류: {str(e)}")
        return []

def get_filings_by_form(form_type):
    """특정 Form 타입의 Filing 가져오기"""
    try:
        # RSS 피드 URL - count 파라미터 추가로 더 많은 결과 가져오기
        rss_url = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type={form_type}&count=100&output=atom"
        
        headers = {
            'User-Agent': 'SEC ETF Monitor Bot/1.0 (Contact: monitor@example.com)',
            'Accept': 'application/atom+xml,application/xml'
        }
        
        response = requests.get(rss_url, headers=headers, timeout=15)
        
        if response.status_code != 200:
            return []
        
        return parse_rss_feed(response.content, form_type)
        
    except Exception as e:
        logger.error(f"{form_type} RSS 오류: {str(e)}")
        return []

def parse_rss_feed(content, form_type):
    """RSS 피드 파싱"""
    filings = []
    
    try:
        root = ET.fromstring(content)
        ns = {'atom': 'http://www.w3.org/2005/Atom'}
        
        entries = root.findall('atom:entry', ns)
        valid_dates = get_date_range()
        
        logger.info(f"{form_type}: {len(entries)}개 엔트리 확인 중...")
        
        for entry in entries[:100]:  # 최대 100개 확인
            try:
                title_elem = entry.find('atom:title', ns)
                summary_elem = entry.find('atom:summary', ns)
                link_elem = entry.find('atom:link', ns)
                updated_elem = entry.find('atom:updated', ns)
                
                if not all([title_elem, link_elem]):
                    continue
                
                title = unescape(title_elem.text or "")
                summary = unescape(summary_elem.text or "") if summary_elem is not None else ""
                link = link_elem.get('href', "")
                updated = updated_elem.text if updated_elem is not None else ""
                
                # 날짜 추출
                filing_date = updated[:10] if updated else ""
                
                # 날짜 필터 (최근 3일)
                if filing_date not in valid_dates:
                    continue
                
                # ETF 관련 체크
                combined_text = (title + " " + summary).lower()
                
                # ETF 관련 키워드
                etf_keywords = ['etf', 'exchange-traded', 'exchange traded', 'index fund']
                if not any(keyword in combined_text for keyword in etf_keywords):
                    continue
                
                # 제외 키워드
                exclude_keywords = ['withdrawal', 'termination', 'liquidation', 'merger', 
                                  'delisting', 'notice of effectiveness', 'prospectus supplement',
                                  'post-effective amendment no']
                
                # 제외 키워드가 있으면 스킵
                if any(keyword in combined_text for keyword in exclude_keywords):
                    continue
                
                # Amendment는 제목에 /A가 있는 경우만 제외
                if "/A" in title and form_type in ["N-1A", "485APOS"]:
                    continue
                
                # ETF 이름 추출
                etf_name = extract_etf_name(title, summary)
                
                if etf_name and etf_name != "Unknown ETF":
                    # Form 타입 추출
                    actual_form = extract_form_type(title) or form_type
                    
                    filing = {
                        "etf_name": etf_name,
                        "filing_type": actual_form,
                        "filing_date": filing_date,
                        "url": link
                    }
                    filings.append(filing)
                    logger.info(f"✅ 발견: {etf_name} ({actual_form}) - {filing_date}")
                
            except Exception as e:
                continue
        
        return filings
        
    except Exception as e:
        logger.error(f"RSS 파싱 오류: {str(e)}")
        return []

def extract_form_type(title):
    """제목에서 Form 타입 추출"""
    form_match = re.search(r'Form\s+([\w-]+)', title, re.IGNORECASE)
    if form_match:
        return form_match.group(1).upper()
    
    # 485APOS, N-1A 등 직접 언급
    form_types = ['485APOS', '485BXT', 'N-1A', 'N-8A', 'N-8B-2', '497']
    for form in form_types:
        if form in title.upper():
            return form
    
    return None

def extract_etf_name(title, summary):
    """ETF 이름 추출"""
    # HTML 엔티티 디코드
    title = unescape(title)
    summary = unescape(summary)
    
    # CIK 번호와 불필요한 텍스트 제거
    clean_title = re.sub(r'\(\d{10}\)', '', title)  # CIK 제거
    clean_title = re.sub(r'\(Filer\)', '', clean_title)  # Filer 제거
    clean_title = re.sub(r'Form\s+[\w-]+', '', clean_title)  # Form 타입 제거
    
    # Summary에서 ETF 이름 찾기
    if summary:
        # Series Name 패턴
        series_match = re.search(r'Series Name[:\s]*([^,\n]+(?:ETF|Exchange[- ]Traded Fund?))', summary, re.IGNORECASE)
        if series_match:
            name = series_match.group(1).strip()
            name = re.sub(r'\s+', ' ', name)
            if len(name) > 5 and 'ETF' in name.upper():
                return name
        
        # Fund Name 패턴
        fund_match = re.search(r'(?:Fund Name|Name of Fund)[:\s]*([^,\n]+(?:ETF|Exchange[- ]Traded Fund?))', summary, re.IGNORECASE)
        if fund_match:
            name = fund_match.group(1).strip()
            name = re.sub(r'\s+', ' ', name)
            if len(name) > 5 and 'ETF' in name.upper():
                return name
    
    # Title에서 ETF 이름 찾기
    # 패턴: 회사명 다음에 오는 ETF 이름
    etf_pattern = re.search(r'([A-Z][A-Za-z0-9\s&\-\.]+(?:ETF|Exchange[- ]Traded Fund?))', clean_title)
    if etf_pattern:
        name = etf_pattern.group(1).strip()
        name = re.sub(r'\s+', ' ', name)
        
        # 회사명 같은 것 제거
        company_keywords = ['Inc', 'Corp', 'LLC', 'Trust', 'Company', 'Partners']
        for keyword in company_keywords:
            name = re.sub(f'\\b{keyword}\\b\\.?', '', name, flags=re.IGNORECASE)
        
        name = name.strip()
        if len(name) > 5 and 'ETF' in name.upper():
            return name
    
    # 대시로 구분된 경우
    parts = re.split(r'\s*[-–—]\s*', clean_title)
    for part in parts:
        if 'etf' in part.lower():
            part = part.strip()
            if len(part) > 5:
                return part
    
    return None

def format_etf_report(filings):
    """ETF 리포트 포맷"""
    korean_time = get_korean_time()
    
    # 어제 날짜 (미국 시간 기준)
    est_yesterday = datetime.now(EST) - timedelta(days=1)
    report_date = est_yesterday.strftime("%Y-%m-%d")
    
    report = f"""📊 <b>SEC ETF 신규 상장신청</b>
━━━━━━━━━━━━━━━━━
📅 {report_date} (미국) | {korean_time.strftime('%H:%M')} KST

"""
    
    if not filings:
        report += """⚠️ 신규 상장신청 없음

최근 3일간 ETF 신규 상장신청이 없습니다."""
    else:
        # 어제 날짜 Filing만 필터링
        yesterday_filings = [f for f in filings if f['filing_date'] == report_date]
        
        if yesterday_filings:
            report += f"""🆕 <b>신규 {len(yesterday_filings)}건</b>

"""
            for filing in yesterday_filings:
                report += f"""• <b>{filing['etf_name']}</b>
  {filing['filing_type']} | <a href="{filing['url']}">SEC Filing →</a>

"""
        else:
            # 어제는 없지만 최근 3일 내 있는 경우
            report += f"""⚠️ 어제({report_date}) 신규 상장신청 없음

최근 3일간 총 {len(filings)}건의 상장신청이 있었습니다."""
    
    return report

@app.route('/')
def home():
    """헬스 체크"""
    return jsonify({
        "status": "healthy",
        "service": "SEC ETF Bot",
        "time": get_korean_time().isoformat()
    })

@app.route('/etf-report', methods=['GET', 'POST'])
def send_etf_report():
    """SEC ETF 리포트 발송"""
    # 중복 실행 방지
    if prevent_duplicate_execution():
        return jsonify({
            "status": "skipped",
            "message": "중복 실행 방지됨"
        }), 200
    
    try:
        logger.info("="*50)
        logger.info(f"ETF 리포트 생성 시작 - {get_korean_time()}")
        
        # 모든 ETF Filing 수집
        filings = get_all_etf_filings()
        logger.info(f"총 {len(filings)}개 Filing 수집 완료")
        
        # 리포트 생성
        report = format_etf_report(filings)
        
        # 텔레그램 전송
        result = send_telegram_message(report)
        
        return jsonify({
            "status": result["status"],
            "message": result["message"],
            "filings_count": len(filings),
            "execution_time": get_korean_time().isoformat()
        })
        
    except Exception as e:
        logger.error(f"리포트 발송 오류: {str(e)}")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

@app.route('/test-sec-data')
def test_sec_data():
    """SEC 데이터 테스트 (상세)"""
    try:
        filings = get_all_etf_filings()
        
        # 날짜별 그룹화
        by_date = {}
        for filing in filings:
            date = filing['filing_date']
            if date not in by_date:
                by_date[date] = []
            by_date[date].append(filing)
        
        return jsonify({
            "status": "success",
            "total": len(filings),
            "by_date": {date: len(items) for date, items in by_date.items()},
            "filings": filings[:20],  # 최대 20개만 표시
            "date_range": get_date_range(),
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
