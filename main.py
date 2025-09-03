import os
import requests
import json
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

# 시간대
KST = timezone(timedelta(hours=9))
EST = timezone(timedelta(hours=-5))

# 신규 ETF Form Types만 (3개)
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

def extract_etf_name_from_filing(filing_url):
    """Filing 문서에서 ETF 이름 추출"""
    try:
        # Filing 페이지 접근
        headers = {
            'User-Agent': 'SEC ETF Monitor Bot/1.0 (admin@example.com)',
            'Accept': 'text/html'
        }
        
        response = requests.get(filing_url, headers=headers, timeout=15)
        
        if response.status_code == 200:
            content = response.text
            
            # ETF 이름 패턴들
            patterns = [
                # Series Name 패턴
                r'Series Name[:\s]*</[^>]+>\s*<[^>]+>([^<]+ETF[^<]*)',
                r'Name of Fund[:\s]*</[^>]+>\s*<[^>]+>([^<]+ETF[^<]*)',
                r'Fund Name[:\s]*</[^>]+>\s*<[^>]+>([^<]+ETF[^<]*)',
                # 테이블에서 찾기
                r'<td[^>]*>([^<]+ETF[^<]*)</td>',
                # 제목에서 찾기
                r'<title>([^<]+ETF[^<]*)</title>',
                # Bold 텍스트에서
                r'<b>([^<]+ETF[^<]*)</b>',
                r'<strong>([^<]+ETF[^<]*)</strong>',
                # 일반 텍스트
                r'>([A-Z][A-Za-z0-9\s&\-\.]+ETF[A-Za-z0-9\s]*)<'
            ]
            
            for pattern in patterns:
                matches = re.findall(pattern, content, re.IGNORECASE)
                if matches:
                    # 가장 적절한 ETF 이름 선택
                    for match in matches:
                        etf_name = unescape(match).strip()
                        # 너무 길거나 이상한 것 제외
                        if 5 < len(etf_name) < 100 and 'ETF' in etf_name.upper():
                            # HTML 태그 제거
                            etf_name = re.sub(r'<[^>]+>', '', etf_name)
                            etf_name = re.sub(r'\s+', ' ', etf_name).strip()
                            return etf_name
            
            logger.debug(f"ETF 이름을 찾을 수 없음: {filing_url}")
            
    except Exception as e:
        logger.error(f"Filing 페이지 접근 오류: {str(e)[:100]}")
    
    return None

def get_daily_index_filings():
    """SEC Daily Index에서 ETF Filing 가져오기"""
    all_filings = []
    
    try:
        yesterday = get_yesterday_date()
        
        # 최근 3일 확인
        for days_back in range(3):
            check_date = yesterday - timedelta(days=days_back)
            
            # Daily Index JSON URL
            year = check_date.strftime('%Y')
            quarter = f"QTR{(check_date.month-1)//3 + 1}"
            date_str = check_date.strftime('%Y%m%d')
            
            index_url = f"https://www.sec.gov/Archives/edgar/daily-index/{year}/{quarter}/master.{date_str}.json"
            
            logger.info(f"Daily Index 조회: {date_str}")
            
            headers = {
                'User-Agent': 'SEC ETF Monitor Bot/1.0 (admin@example.com)',
                'Accept': 'application/json'
            }
            
            try:
                response = requests.get(index_url, headers=headers, timeout=20)
                
                if response.status_code == 200:
                    data = response.json()
                    
                    # Filing 정보 파싱
                    for item in data.get('item', []):
                        form_type = item.get('type', '').upper()
                        
                        # 3개 Form Type만 체크
                        if any(etf_form in form_type for etf_form in ETF_FORMS):
                            # Amendment 제외 (/A)
                            if '/A' not in form_type:
                                
                                company_name = item.get('company', 'Unknown')
                                cik = item.get('cik', '')
                                date_filed = item.get('date', check_date.strftime('%Y-%m-%d'))
                                
                                # Filing URL 생성
                                accession = item.get('accession', '').replace('.txt', '')
                                if accession:
                                    filing_url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}/{accession}-index.htm"
                                    
                                    # Filing 페이지에서 실제 ETF 이름 추출 시도
                                    etf_name = extract_etf_name_from_filing(filing_url)
                                    
                                    # 못 찾으면 회사명에서 추출
                                    if not etf_name:
                                        etf_name = extract_etf_name_from_company(company_name)
                                else:
                                    filing_url = "#"
                                    etf_name = extract_etf_name_from_company(company_name)
                                
                                filing = {
                                    "etf_name": etf_name,
                                    "filing_type": form_type,
                                    "filing_date": date_filed,
                                    "url": filing_url,
                                    "company": company_name
                                }
                                
                                all_filings.append(filing)
                                logger.info(f"✅ 발견: {etf_name} ({form_type})")
                                
            except Exception as e:
                logger.error(f"Index 조회 실패 {date_str}: {str(e)[:100]}")
                continue
                
    except Exception as e:
        logger.error(f"Daily Index 오류: {str(e)}")
    
    # 중복 제거
    unique_filings = []
    seen_urls = set()
    for f in all_filings:
        if f['url'] not in seen_urls:
            seen_urls.add(f['url'])
            unique_filings.append(f)
    
    return unique_filings

def extract_etf_name_from_company(company_name):
    """회사명에서 ETF 이름 추출 (백업)"""
    # 정리
    name = company_name
    name = re.sub(r'\(Filer\)', '', name)
    name = re.sub(r'\(\d{10}\)', '', name)  # CIK 제거
    
    # ETF가 포함된 경우
    if 'ETF' in name.upper():
        match = re.search(r'([A-Za-z][A-Za-z0-9\s&\-\.]*ETF[A-Za-z0-9\s]*)', name, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    
    # Trust, Fund 등
    if any(word in name.upper() for word in ['TRUST', 'FUND', 'SERIES']):
        return name.strip()[:60]
    
    return name.split('-')[0].strip()[:60]

def format_etf_report(filings):
    """리포트 포맷"""
    korean_time = get_korean_time()
    yesterday = get_yesterday_date().strftime('%Y-%m-%d')
    
    report = f"""<b>SEC ETF 신규 상장신청</b>
───────────────────
📅 {yesterday} (미국) | {korean_time.strftime('%H:%M')} KST

"""
    
    if not filings:
        report += """⚠️ 신규 상장신청 없음

어제는 ETF 신규 상장신청이 없었습니다.

📌 Form Types: 485APOS, 485BPOS, N-1A"""
    else:
        # 어제 날짜만 필터
        yesterday_filings = [f for f in filings if yesterday in f.get('filing_date', '')]
        
        if not yesterday_filings:
            # 최근 3일 중 최신
            yesterday_filings = filings[:10]
            report += f"""📌 최근 3일간 Filing:

"""
        else:
            report += f"""🆕 <b>신규 {len(yesterday_filings)}건</b>

"""
        
        for filing in yesterday_filings:
            # ETF 이름 표시
            display_name = filing['etf_name']
            
            # 회사명이 다르고 ETF 이름이 일반적이면 회사명도 표시
            if filing.get('company') and filing['company'] != filing['etf_name']:
                if len(filing['etf_name']) < 20:  # 짧은 이름이면
                    display_name = f"{filing['company']} - {filing['etf_name']}"
            
            report += f"""  • <b>{display_name}</b>
    {filing['filing_type']} | {filing['filing_date']} | <a href="{filing['url']}">SEC →</a>

"""
    
    return report

@app.route('/')
def home():
    return jsonify({
        "status": "healthy",
        "service": "SEC ETF Bot",
        "time": get_korean_time().isoformat(),
        "forms_tracked": ETF_FORMS
    })

@app.route('/etf-report')
def send_etf_report():
    """ETF 리포트 발송"""
    try:
        logger.info("="*50)
        logger.info(f"리포트 생성: {get_korean_time()}")
        logger.info(f"추적 Form Types: {ETF_FORMS}")
        
        # Filing 수집
        filings = get_daily_index_filings()
        logger.info(f"수집 완료: {len(filings)}개")
        
        # 리포트 생성 및 전송
        report = format_etf_report(filings)
        result = send_telegram_message(report)
        
        return jsonify({
            "status": "success",
            "total": len(filings),
            "yesterday": get_yesterday_date().strftime('%Y-%m-%d'),
            "forms_tracked": ETF_FORMS,
            "filings": filings[:10]
        })
        
    except Exception as e:
        logger.error(f"오류: {str(e)}")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

@app.route('/test-sec-data')
def test_sec_data():
    """데이터 테스트"""
    try:
        filings = get_daily_index_filings()
        
        # 날짜별 그룹
        by_date = {}
        for f in filings:
            date = f.get('filing_date', 'unknown')
            if date not in by_date:
                by_date[date] = []
            by_date[date].append({
                "name": f['etf_name'],
                "type": f['filing_type']
            })
        
        # Form별 집계
        by_form = {}
        for f in filings:
            form = f['filing_type']
            if form not in by_form:
                by_form[form] = 0
            by_form[form] += 1
        
        return jsonify({
            "status": "success",
            "total": len(filings),
            "yesterday": get_yesterday_date().strftime('%Y-%m-%d'),
            "forms_tracked": ETF_FORMS,
            "by_date": by_date,
            "by_form": by_form,
            "all_filings": filings
        })
        
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
