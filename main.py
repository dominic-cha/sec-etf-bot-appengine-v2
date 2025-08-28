import os
import requests
import json
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from flask import Flask, jsonify, request
import logging
import re

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
    """어제 날짜 반환 (미국 동부시간 기준)"""
    est_now = datetime.now(EST)
    yesterday = est_now - timedelta(days=1)
    return yesterday.strftime("%Y-%m-%d")

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

def get_all_n1a_filings():
    """SEC에서 모든 운용사의 N-1A 신규 상장신청 가져오기"""
    all_filings = []
    
    # 1. N-1A Form RSS 피드 (신규 등록)
    all_filings.extend(get_n1a_rss_feed())
    
    # 2. 485APOS Form RSS 피드 (신규 ETF 클래스 추가)
    all_filings.extend(get_485apos_rss_feed())
    
    # 3. 중복 제거 (URL 기준)
    unique_filings = []
    seen_urls = set()
    for filing in all_filings:
        if filing['url'] not in seen_urls:
            seen_urls.add(filing['url'])
            unique_filings.append(filing)
    
    # 날짜순 정렬 (최신순)
    unique_filings.sort(key=lambda x: x['filing_date'], reverse=True)
    
    return unique_filings

def get_n1a_rss_feed():
    """N-1A Form RSS 피드 파싱 (초기 등록)"""
    try:
        # N-1A는 신규 펀드 등록
        rss_url = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=N-1A&output=atom"
        
        headers = {
            'User-Agent': 'SEC ETF Monitor Bot/1.0 (Contact: monitor@example.com)',
            'Accept': 'application/atom+xml,application/xml'
        }
        
        logger.info(f"N-1A RSS 피드 요청...")
        response = requests.get(rss_url, headers=headers, timeout=15)
        
        if response.status_code != 200:
            logger.error(f"N-1A RSS 피드 접근 실패: {response.status_code}")
            return []
        
        filings = parse_rss_feed(response.content, "N-1A")
        logger.info(f"N-1A에서 {len(filings)}개 신규 상장신청 발견")
        return filings
        
    except Exception as e:
        logger.error(f"N-1A RSS 처리 오류: {str(e)}")
        return []

def get_485apos_rss_feed():
    """485APOS Form RSS 피드 파싱 (신규 ETF 클래스)"""
    try:
        # 485APOS는 기존 펀드의 신규 ETF 클래스 추가
        rss_url = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=485APOS&output=atom"
        
        headers = {
            'User-Agent': 'SEC ETF Monitor Bot/1.0 (Contact: monitor@example.com)',
            'Accept': 'application/atom+xml,application/xml'
        }
        
        logger.info(f"485APOS RSS 피드 요청...")
        response = requests.get(rss_url, headers=headers, timeout=15)
        
        if response.status_code != 200:
            logger.error(f"485APOS RSS 피드 접근 실패: {response.status_code}")
            return []
        
        filings = parse_rss_feed(response.content, "485APOS")
        logger.info(f"485APOS에서 {len(filings)}개 신규 ETF 발견")
        return filings
        
    except Exception as e:
        logger.error(f"485APOS RSS 처리 오류: {str(e)}")
        return []

def parse_rss_feed(content, form_type):
    """RSS 피드 공통 파싱 로직"""
    filings = []
    
    try:
        # XML 파싱
        root = ET.fromstring(content)
        ns = {'atom': 'http://www.w3.org/2005/Atom'}
        
        entries = root.findall('atom:entry', ns)
        yesterday = get_yesterday_date()
        
        for entry in entries:
            try:
                title = entry.find('atom:title', ns).text if entry.find('atom:title', ns) is not None else ""
                summary = entry.find('atom:summary', ns).text if entry.find('atom:summary', ns) is not None else ""
                link = entry.find('atom:link', ns).get('href') if entry.find('atom:link', ns) is not None else ""
                updated = entry.find('atom:updated', ns).text if entry.find('atom:updated', ns) is not None else ""
                
                # ETF 관련 키워드 확인 (대소문자 무시)
                etf_keywords = ['ETF', 'Exchange-Traded', 'Exchange Traded', 'Index Fund', 
                              'SPDR', 'iShares', 'PowerShares', 'ProShares', 'VanEck']
                
                title_lower = title.lower()
                summary_lower = summary.lower()
                
                is_etf = any(keyword.lower() in title_lower or keyword.lower() in summary_lower 
                           for keyword in etf_keywords)
                
                # 수정/변경 관련 키워드 확인 (제외할 항목)
                exclude_keywords = ['Amendment', 'Supplement', 'Withdrawal', 'Correction', 
                                  'Termination', 'Liquidation', 'Merger', 'Name Change']
                is_amendment = any(keyword.lower() in title_lower for keyword in exclude_keywords)
                
                # N-1A/A는 수정본이므로 제외, N-1A만 포함
                if form_type == "N-1A" and "/A" in title:
                    is_amendment = True
                
                # 날짜 필터링 (어제 또는 오늘)
                filing_date = updated[:10] if updated else ""
                
                if is_etf and not is_amendment and filing_date >= yesterday:
                    # 회사명 추출 (첫 번째 "-" 이전 부분)
                    company_match = re.search(r'^([^-–—]+)', title)
                    company_name = company_match.group(1).strip() if company_match else "Unknown"
                    
                    # ETF 이름 추출 시도
                    etf_name = extract_etf_name(title, summary)
                    
                    filing = {
                        "company": company_name,
                        "etf_name": etf_name,
                        "filing_type": form_type,
                        "filing_date": filing_date,
                        "url": link
                    }
                    filings.append(filing)
                    
            except Exception as e:
                logger.error(f"엔트리 파싱 오류: {str(e)}")
                continue
    
    except Exception as e:
        logger.error(f"RSS 파싱 오류: {str(e)}")
    
    return filings

def extract_etf_name(title, summary):
    """제목과 요약에서 ETF 이름 추출"""
    # ETF 이름 패턴 매칭
    patterns = [
        r'([A-Za-z\s]+(?:ETF|Exchange-Traded Fund|Exchange Traded Fund))',
        r'([A-Za-z\s]+Fund)',
        r'([A-Za-z\s]+Trust)'
    ]
    
    for pattern in patterns:
        match = re.search(pattern, title, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    
    # 제목에서 회사명 이후 부분 추출
    parts = re.split(r'[-–—]', title)
    if len(parts) > 1:
        potential_name = parts[1].strip()
        # Form 타입 제거
        potential_name = re.sub(r'\(Form.*?\)', '', potential_name).strip()
        potential_name = re.sub(r'Form\s+\S+', '', potential_name).strip()
        if potential_name and len(potential_name) > 5:
            return potential_name
    
    return "ETF Registration"

def format_etf_report(filings):
    """간결한 ETF 리포트 포맷"""
    korean_time = get_korean_time()
    yesterday = get_yesterday_date()
    
    report = f"""📊 <b>SEC ETF 신규 상장신청</b>
━━━━━━━━━━━━━━━━━
📅 {yesterday} (미국시간)
🕐 {korean_time.strftime('%H:%M')} KST

"""
    
    if not filings:
        report += """⚠️ 신규 상장신청 없음

어제는 ETF 신규 상장신청이 제출되지 않았습니다."""
    else:
        report += f"""🆕 <b>신규 {len(filings)}건</b>

"""
        for filing in filings:
            # 회사명과 ETF 이름 조합
            display_name = filing['etf_name']
            if filing['etf_name'] == "ETF Registration":
                display_name = filing['company']
            elif filing['company'] not in filing['etf_name']:
                display_name = f"{filing['company']} - {filing['etf_name']}"
            
            report += f"""• <b>{display_name}</b>
  {filing['filing_type']} | {filing['filing_date']}
  <a href="{filing['url']}">SEC Filing →</a>

"""
    
    report += f"""
━━━━━━━━━━━━━━━━━
🔄 내일 오전 8시 발송"""
    
    return report

@app.route('/')
def home():
    """헬스 체크"""
    return jsonify({
        "status": "healthy",
        "service": "SEC ETF Bot",
        "time": get_korean_time().isoformat()
    })

@app.route('/test-telegram')
def test_telegram():
    """텔레그램 연결 테스트"""
    test_message = f"""🔧 <b>텔레그램 연결 테스트</b>

✅ 봇 연결 성공!
⏰ 한국시간: {get_korean_time().strftime('%Y-%m-%d %H:%M:%S')}"""
    
    result = send_telegram_message(test_message)
    return jsonify(result)

@app.route('/etf-report')
def send_etf_report():
    """SEC ETF 리포트 발송"""
    try:
        logger.info("ETF 리포트 생성 시작...")
        
        # 모든 운용사의 N-1A 신규 상장신청 수집
        filings = get_all_n1a_filings()
        logger.info(f"총 {len(filings)}개 신규 상장신청 수집")
        
        # 리포트 생성
        report = format_etf_report(filings)
        
        # 텔레그램 전송
        result = send_telegram_message(report)
        
        return jsonify({
            "status": result["status"],
            "message": result["message"],
            "filings_count": len(filings),
            "timestamp": get_korean_time().isoformat()
        })
        
    except Exception as e:
        logger.error(f"리포트 발송 중 오류: {str(e)}")
        return jsonify({
            "status": "error",
            "message": f"리포트 발송 실패: {str(e)}"
        }), 500

@app.route('/test-sec-data')
def test_sec_data():
    """SEC 데이터 수집 테스트"""
    try:
        # 모든 신규 상장신청 수집
        filings = get_all_n1a_filings()
        
        return jsonify({
            "status": "success",
            "total_count": len(filings),
            "filings": filings,
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
