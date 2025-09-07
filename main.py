import os
import requests
import json
from datetime import datetime, timezone, timedelta
from flask import Flask, jsonify, request
import logging
import re
from html import unescape
import urllib.parse

# 로깅 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# 환경 변수
BOT_TOKEN = os.environ.get('BOT_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')

# 시간대
KST = timezone(timedelta(hours=9))
EST = timezone(timedelta(hours=-5))

# 신규 ETF Form Types만
ETF_FORMS = ['485APOS', '485BPOS', 'N-1A']

def get_korean_time():
    return datetime.now(KST)

def get_yesterday_date():
    """어제 날짜 (평일)"""
    est_now = datetime.now(EST)
    yesterday = est_now - timedelta(days=1)
    # 주말이면 금요일로
    while yesterday.weekday() > 4:
        yesterday -= timedelta(days=1)
    return yesterday

def send_telegram_message(message):
    """텔레그램 메시지 전송"""
    if not BOT_TOKEN or not CHAT_ID:
        return {"status": "error", "message": "환경변수 누락"}
    
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    
    payload = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": False
    }
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            logger.info("✅ 텔레그램 전송 성공")
            return {"status": "success"}
        else:
            logger.error(f"❌ 텔레그램 오류: {response.text}")
            return {"status": "error"}
    except Exception as e:
        logger.error(f"❌ 전송 오류: {str(e)}")
        return {"status": "error", "message": str(e)}

def get_edgar_search_results():
    """EDGAR Search를 통한 직접 검색"""
    all_filings = []
    
    try:
        # EDGAR Search API 사용
        base_url = "https://efts.sec.gov/LATEST/search-index"
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/json',
            'Content-Type': 'application/json',
            'Origin': 'https://www.sec.gov',
            'Referer': 'https://www.sec.gov/'
        }
        
        # 각 Form Type별로 검색
        for form_type in ETF_FORMS:
            try:
                # 검색 파라미터
                payload = {
                    "q": "",
                    "dateRange": "custom",
                    "startdt": (datetime.now(EST) - timedelta(days=5)).strftime("%Y-%m-%d"),
                    "enddt": datetime.now(EST).strftime("%Y-%m-%d"),
                    "forms": [form_type],
                    "page": "1",
                    "from": 0,
                    "size": 100,
                    "sort": [{"filedAt": {"order": "desc"}}]
                }
                
                logger.info(f"EDGAR Search: {form_type}")
                
                response = requests.post(
                    base_url, 
                    json=payload, 
                    headers=headers, 
                    timeout=20
                )
                
                if response.status_code == 200:
                    data = response.json()
                    hits = data.get('hits', {}).get('hits', [])
                    
                    for hit in hits:
                        source = hit.get('_source', {})
                        
                        # Filing 정보 추출
                        company_name = source.get('display_names', ['Unknown'])[0]
                        filing_date = source.get('file_date', '')
                        form = source.get('file_type', form_type)
                        cik = source.get('ciks', [''])[0]
                        file_num = source.get('file_num', '')
                        accession = source.get('accession_number', '')
                        
                        # URL 생성
                        if accession and cik:
                            filing_url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}/{accession}-index.htm"
                        else:
                            filing_url = "#"
                        
                        # ETF 관련인지 체크
                        if 'ETF' in company_name.upper() or 'EXCHANGE' in company_name.upper() or 'FUND' in company_name.upper():
                            etf_name = extract_etf_name_from_text(company_name)
                            
                            filing = {
                                "etf_name": etf_name,
                                "filing_type": form,
                                "filing_date": filing_date,
                                "url": filing_url,
                                "company": company_name
                            }
                            
                            all_filings.append(filing)
                            logger.info(f"✅ 발견: {etf_name} ({form})")
                
                else:
                    logger.error(f"Search API 응답 오류: {response.status_code}")
                    
            except Exception as e:
                logger.error(f"{form_type} 검색 오류: {str(e)[:100]}")
                continue
        
    except Exception as e:
        logger.error(f"EDGAR Search 오류: {str(e)}")
    
    # 백업: Latest Filings 페이지 직접 파싱
    if not all_filings:
        logger.info("EDGAR Search 실패, Latest Filings 시도...")
        all_filings = scrape_latest_filings()
    
    return all_filings

def scrape_latest_filings():
    """Latest Filings 페이지 직접 스크래핑"""
    filings = []
    
    try:
        # Latest Filings 페이지
        url = "https://www.sec.gov/cgi-bin/browse-edgar"
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'text/html,application/xhtml+xml'
        }
        
        # 각 Form Type별로 조회
        for form_type in ETF_FORMS:
            params = {
                'action': 'getcurrent',
                'type': form_type,
                'company': '',
                'dateb': '',
                'owner': 'include',
                'start': '0',
                'count': '100',
                'output': 'atom'
            }
            
            response = requests.get(url, params=params, headers=headers, timeout=20)
            
            if response.status_code == 200:
                # HTML/XML 파싱
                content = response.text
                
                # Entry 찾기
                entries = re.findall(r'<entry>(.*?)</entry>', content, re.DOTALL)
                
                for entry in entries:
                    # 정보 추출
                    title_match = re.search(r'<title>(.*?)</title>', entry)
                    link_match = re.search(r'<link[^>]*href="([^"]+)"', entry)
                    updated_match = re.search(r'<updated>(.*?)</updated>', entry)
                    
                    if title_match and link_match:
                        title = unescape(title_match.group(1))
                        link = link_match.group(1)
                        date = updated_match.group(1)[:10] if updated_match else get_yesterday_date().strftime('%Y-%m-%d')
                        
                        # ETF 관련 체크
                        if any(keyword in title.upper() for keyword in ['ETF', 'EXCHANGE', 'FUND', 'TRUST']):
                            etf_name = extract_etf_name_from_text(title)
                            
                            filing = {
                                "etf_name": etf_name,
                                "filing_type": form_type,
                                "filing_date": date,
                                "url": link if link.startswith('http') else f"https://www.sec.gov{link}"
                            }
                            
                            filings.append(filing)
                            logger.info(f"Latest Filing: {etf_name}")
            
    except Exception as e:
        logger.error(f"Latest Filings 스크래핑 오류: {str(e)}")
    
    return filings

def extract_etf_name_from_text(text):
    """텍스트에서 ETF 이름 추출"""
    # 정리
    text = unescape(text)
    text = re.sub(r'\([0-9]{10}\)', '', text)  # CIK 제거
    text = re.sub(r'\(Filer\)', '', text)
    text = re.sub(r'Form\s+[\w/-]+', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\s*[-–—]\s*', ' - ', text)
    
    # ETF 패턴 찾기
    etf_match = re.search(r'([A-Za-z][A-Za-z0-9\s&\-\.]*(?:ETF|Fund|Trust)[A-Za-z0-9\s]*)', text, re.IGNORECASE)
    if etf_match:
        name = etf_match.group(1).strip()
        name = re.sub(r'\s+', ' ', name)
        if len(name) > 5:
            return name[:80]
    
    # 첫 부분 사용
    parts = text.split('-')
    if parts:
        name = parts[0].strip()
        if len(name) > 5:
            return name[:80]
    
    return "ETF Filing"

def format_etf_report(filings):
    """리포트 포맷"""
    korean_time = get_korean_time()
    yesterday = get_yesterday_date().strftime('%Y-%m-%d')
    
    report = f"""<b>SEC ETF 신규 상장신청</b>
──────────────
📅 {yesterday} (미국) | {korean_time.strftime('%H:%M')} KST

"""
    
    if not filings:
        report += """⚠️ 신규 상장신청 없음

어제는 ETF 신규 상장신청이 없었습니다.

📌 Form Types: 485APOS, 485BPOS, N-1A
💡 데이터 소스: SEC EDGAR"""
    else:
        # 날짜별 필터링
        yesterday_filings = [f for f in filings if yesterday in f.get('filing_date', '')]
        
        # 어제 것이 없으면 최근 7일
        if not yesterday_filings:
            recent_filings = filings[:15]  # 최대 15개
            if recent_filings:
                report += f"""📌 최근 7일간 Filing ({len(filings)}건):

"""
                for filing in recent_filings:
                    report += f"""  • <b>{filing['etf_name']}</b>
    {filing['filing_type']} | {filing['filing_date']} | <a href="{filing['url']}">SEC →</a>

"""
        else:
            report += f"""🆕 <b>신규 {len(yesterday_filings)}건</b>

"""
            for filing in yesterday_filings:
                report += f"""  • <b>{filing['etf_name']}</b>
    {filing['filing_type']} | <a href="{filing['url']}">SEC →</a>

"""
    
    return report

@app.route('/')
def home():
    return jsonify({
        "status": "healthy",
        "service": "SEC ETF Bot",
        "time": get_korean_time().isoformat(),
        "forms": ETF_FORMS
    })

@app.route('/etf-report')
def send_etf_report():
    """ETF 리포트 발송"""
    try:
        logger.info("="*50)
        logger.info(f"리포트 생성: {get_korean_time()}")
        
        # Filing 수집
        filings = get_edgar_search_results()
        logger.info(f"수집 완료: {len(filings)}개")
        
        # 리포트 생성 및 전송
        report = format_etf_report(filings)
        result = send_telegram_message(report)
        
        return jsonify({
            "status": "success",
            "total": len(filings),
            "forms": ETF_FORMS,
            "filings": filings[:10]
        })
        
    except Exception as e:
        logger.error(f"오류: {str(e)}")
        
        # 오류 메시지도 전송
        error_report = f"""<b>SEC ETF 신규 상장신청</b>
──────────────
📅 {get_yesterday_date().strftime('%Y-%m-%d')} (미국) | {get_korean_time().strftime('%H:%M')} KST

❌ 데이터 수집 오류

시스템 점검이 필요합니다."""
        
        send_telegram_message(error_report)
        
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

@app.route('/test-sec-data')
def test_sec_data():
    """데이터 테스트"""
    try:
        filings = get_edgar_search_results()
        
        # 날짜별 그룹
        by_date = {}
        for f in filings:
            date = f.get('filing_date', 'unknown')
            if date not in by_date:
                by_date[date] = []
            by_date[date].append(f['etf_name'])
        
        return jsonify({
            "status": "success",
            "total": len(filings),
            "yesterday": get_yesterday_date().strftime('%Y-%m-%d'),
            "forms": ETF_FORMS,
            "by_date": by_date,
            "filings": filings,
            "test_urls": [
                "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=485APOS",
                "https://efts.sec.gov/LATEST/search-index"
            ]
        })
        
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e),
            "forms": ETF_FORMS
        }), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
