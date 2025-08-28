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

def get_date_range():
    """조회 날짜 범위 (어제와 오늘)"""
    est_now = datetime.now(EST)
    yesterday = est_now - timedelta(days=1)
    today = est_now
    return yesterday.strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d")

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

def fetch_filing_detail(url):
    """Filing 상세 페이지에서 ETF 이름 추출"""
    try:
        headers = {
            'User-Agent': 'SEC ETF Monitor Bot/1.0 (Contact: monitor@example.com)'
        }
        
        # Filing 페이지 가져오기
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code != 200:
            return None
        
        content = response.text
        
        # ETF 이름 패턴 찾기 (여러 패턴 시도)
        patterns = [
            # Tuttle Capital Ethereum Income Blast ETF 같은 패턴
            r'<b>([^<]+(?:ETF|Exchange[- ]Traded Fund?))</b>',
            r'Name of Fund[:\s]*([^<\n]+(?:ETF|Exchange[- ]Traded Fund?))',
            r'Series Name[:\s]*([^<\n]+(?:ETF|Exchange[- ]Traded Fund?))',
            r'>([^<]+(?:ETF|Exchange[- ]Traded Fund?))</(?:b|strong)>',
            # 제목에서 찾기
            r'<title>([^<]+(?:ETF|Exchange[- ]Traded Fund?))[^<]*</title>',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, content, re.IGNORECASE)
            if match:
                etf_name = match.group(1).strip()
                # HTML 엔티티 제거
                etf_name = unescape(etf_name)
                # 불필요한 문자 제거
                etf_name = re.sub(r'\s+', ' ', etf_name)
                etf_name = re.sub(r'^\W+|\W+$', '', etf_name)
                if etf_name and 'ETF' in etf_name.upper():
                    return etf_name
        
        return None
        
    except Exception as e:
        logger.error(f"Filing 상세 페이지 가져오기 실패: {str(e)}")
        return None

def get_all_etf_filings():
    """SEC에서 모든 ETF 관련 신규 상장신청 가져오기"""
    all_filings = []
    
    # Form 타입별 수집
    form_types = ["N-1A", "485APOS", "485BXT", "497"]
    
    for form_type in form_types:
        filings = get_filings_by_form(form_type)
        all_filings.extend(filings)
        logger.info(f"{form_type}: {len(filings)}개 수집")
    
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

def get_filings_by_form(form_type):
    """특정 Form 타입의 Filing 가져오기"""
    try:
        # RSS 피드 URL
        rss_url = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type={form_type}&output=atom"
        
        headers = {
            'User-Agent': 'SEC ETF Monitor Bot/1.0 (Contact: monitor@example.com)',
            'Accept': 'application/atom+xml,application/xml'
        }
        
        response = requests.get(rss_url, headers=headers, timeout=15)
        
        if response.status_code != 200:
            return []
        
        # XML 파싱
        try:
            root = ET.fromstring(response.content)
        except ET.ParseError:
            return []
        
        ns = {'atom': 'http://www.w3.org/2005/Atom'}
        
        filings = []
        entries = root.findall('atom:entry', ns)
        
        yesterday, today = get_date_range()
        
        for entry in entries[:20]:  # 최근 20개만 처리
            try:
                # 기본 정보 추출
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
                
                # 날짜 필터
                filing_date = updated[:10] if updated else ""
                if not filing_date or (filing_date != yesterday and filing_date != today):
                    continue
                
                # ETF 관련 여부 체크
                combined_text = (title + " " + summary).lower()
                etf_indicators = ['etf', 'exchange-traded', 'exchange traded']
                
                if not any(indicator in combined_text for indicator in etf_indicators):
                    continue
                
                # Amendment 제외
                if "/A" in title and form_type in ["N-1A", "485APOS"]:
                    continue
                
                # 제외 키워드
                exclude_keywords = ['withdrawal', 'termination', 'liquidation', 'merger', 'delisting', 'notice of effectiveness']
                if any(word in combined_text for word in exclude_keywords):
                    continue
                
                # ETF 이름 추출 - 여러 방법 시도
                etf_name = None
                
                # 방법 1: Summary에서 ETF 이름 찾기
                if summary:
                    # "Series Name: Tuttle Capital Ethereum Income Blast ETF" 패턴
                    series_match = re.search(r'Series Name[:\s]*([^,\n]+(?:ETF|Exchange[- ]Traded Fund?))', summary, re.IGNORECASE)
                    if series_match:
                        etf_name = series_match.group(1).strip()
                    
                    # 일반 ETF 이름 패턴
                    if not etf_name:
                        etf_match = re.search(r'([A-Z][A-Za-z0-9\s&\-\.]+(?:ETF|Exchange[- ]Traded Fund?))', summary)
                        if etf_match:
                            etf_name = etf_match.group(1).strip()
                
                # 방법 2: Title에서 추출
                if not etf_name:
                    # CIK 번호와 (Filer) 제거
                    clean_title = re.sub(r'\(\d{10}\)', '', title)  # CIK 제거
                    clean_title = re.sub(r'\(Filer\)', '', clean_title)  # Filer 제거
                    clean_title = re.sub(r'\s*[-–—]\s*', ' - ', clean_title)  # 대시 정규화
                    
                    # 회사명과 ETF 이름 분리
                    parts = clean_title.split(' - ')
                    
                    # ETF 이름 찾기
                    for part in parts:
                        if 'etf' in part.lower() and 'form' not in part.lower():
                            etf_name = part.strip()
                            break
                    
                    # 못 찾았으면 첫 번째 부분 사용
                    if not etf_name and len(parts) > 0:
                        potential_name = parts[0].strip()
                        # Form 타입 제거
                        potential_name = re.sub(r'Form\s+[\w/]+', '', potential_name, flags=re.IGNORECASE).strip()
                        if 'etf' in potential_name.lower():
                            etf_name = potential_name
                
                # 방법 3: 상세 페이지에서 추출 (느릴 수 있음 - 선택적)
                if not etf_name and len(filings) < 5:  # 처음 5개만
                    fetched_name = fetch_filing_detail(link)
                    if fetched_name:
                        etf_name = fetched_name
                
                # ETF 이름 정리
                if etf_name:
                    etf_name = re.sub(r'\s+', ' ', etf_name).strip()
                    etf_name = re.sub(r'^[^\w]+|[^\w]+$', '', etf_name).strip()
                    
                    # 너무 짧거나 Form 타입만 있으면 제외
                    if len(etf_name) < 5 or etf_name.upper() == form_type:
                        etf_name = None
                
                # 최종 Filing 객체 생성
                if etf_name:  # ETF 이름이 있는 경우만 포함
                    filing = {
                        "etf_name": etf_name,
                        "filing_type": form_type,
                        "filing_date": filing_date,
                        "url": link
                    }
                    filings.append(filing)
                    logger.info(f"발견: {etf_name} ({form_type})")
                
            except Exception as e:
                logger.error(f"엔트리 파싱 오류: {str(e)}")
                continue
        
        return filings
        
    except Exception as e:
        logger.error(f"{form_type} RSS 처리 오류: {str(e)}")
        return []

def format_etf_report(filings):
    """간결한 ETF 리포트 포맷"""
    korean_time = get_korean_time()
    yesterday, today = get_date_range()
    
    report = f"""📊 <b>SEC ETF 신규 상장신청</b>
━━━━━━━━━━━━━━━━━
📅 {yesterday} (미국) | {korean_time.strftime('%H:%M')} KST

"""
    
    if not filings:
        report += """⚠️ 신규 상장신청 없음

ETF 신규 상장신청이 없습니다."""
    else:
        report += f"""🆕 <b>신규 {len(filings)}건</b>

"""
        for filing in filings:
            report += f"""• <b>{filing['etf_name']}</b>
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
        logger.info("="*50)
        logger.info("ETF 리포트 생성 시작...")
        
        # 모든 ETF 신규 상장신청 수집
        filings = get_all_etf_filings()
        logger.info(f"총 {len(filings)}개 신규 상장신청 수집 완료")
        
        # 리포트 생성
        report = format_etf_report(filings)
        
        # 텔레그램 전송
        result = send_telegram_message(report)
        
        return jsonify({
            "status": result["status"],
            "message": result["message"],
            "filings_count": len(filings),
            "filings": filings,  # 디버깅용
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
        filings = get_all_etf_filings()
        
        return jsonify({
            "status": "success",
            "total": len(filings),
            "filings": filings,
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
