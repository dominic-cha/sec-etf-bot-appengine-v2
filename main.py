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

def get_all_etf_filings():
    """SEC에서 모든 ETF 관련 신규 상장신청 가져오기"""
    all_filings = []
    
    # 1. N-1A Form (신규 펀드 등록)
    n1a_filings = get_filings_by_form("N-1A")
    all_filings.extend(n1a_filings)
    logger.info(f"N-1A: {len(n1a_filings)}개")
    
    # 2. 485APOS Form (신규 ETF 클래스 추가)
    apos_filings = get_filings_by_form("485APOS")
    all_filings.extend(apos_filings)
    logger.info(f"485APOS: {len(apos_filings)}개")
    
    # 3. 485BXT Form (ETF 신규 시리즈)
    bxt_filings = get_filings_by_form("485BXT")
    all_filings.extend(bxt_filings)
    logger.info(f"485BXT: {len(bxt_filings)}개")
    
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
        
        logger.info(f"{form_type} RSS 피드 요청 중...")
        response = requests.get(rss_url, headers=headers, timeout=15)
        
        if response.status_code != 200:
            logger.error(f"{form_type} RSS 접근 실패: {response.status_code}")
            return []
        
        # XML 파싱
        try:
            root = ET.fromstring(response.content)
        except ET.ParseError as e:
            logger.error(f"XML 파싱 오류: {e}")
            return []
        
        # Namespace 정의
        ns = {'atom': 'http://www.w3.org/2005/Atom'}
        
        filings = []
        entries = root.findall('atom:entry', ns)
        
        yesterday, today = get_date_range()
        
        for entry in entries:
            try:
                # 기본 정보 추출
                title_elem = entry.find('atom:title', ns)
                summary_elem = entry.find('atom:summary', ns)
                link_elem = entry.find('atom:link', ns)
                updated_elem = entry.find('atom:updated', ns)
                
                if title_elem is None or link_elem is None:
                    continue
                
                title = unescape(title_elem.text or "")
                summary = unescape(summary_elem.text or "") if summary_elem is not None else ""
                link = link_elem.get('href', "")
                updated = updated_elem.text if updated_elem is not None else ""
                
                # 날짜 추출
                filing_date = updated[:10] if updated else ""
                
                # 날짜 필터 (어제 또는 오늘)
                if not filing_date or (filing_date != yesterday and filing_date != today):
                    continue
                
                # ETF 키워드 체크
                combined_text = (title + " " + summary).lower()
                etf_indicators = ['etf', 'exchange-traded', 'exchange traded', 'index fund']
                
                if not any(indicator in combined_text for indicator in etf_indicators):
                    continue
                
                # Amendment 제외 (/A로 끝나는 것)
                if form_type == "N-1A" and "/A" in title:
                    continue
                
                # 제외 키워드 체크
                exclude_keywords = ['withdrawal', 'termination', 'liquidation', 'merger', 'delisting']
                if any(word in combined_text for word in exclude_keywords):
                    continue
                
                # 회사명과 ETF 이름 추출
                company_name, etf_name = parse_filing_title(title, summary)
                
                # 디버깅 로그
                logger.info(f"발견: {company_name} - {etf_name} ({form_type})")
                
                filing = {
                    "company": company_name,
                    "etf_name": etf_name,
                    "filing_type": form_type,
                    "filing_date": filing_date,
                    "url": link,
                    "raw_title": title[:100]  # 디버깅용
                }
                filings.append(filing)
                
            except Exception as e:
                logger.error(f"엔트리 파싱 오류: {str(e)}")
                continue
        
        return filings
        
    except Exception as e:
        logger.error(f"{form_type} RSS 처리 오류: {str(e)}")
        return []

def parse_filing_title(title, summary):
    """Filing 제목에서 회사명과 ETF 이름 추출"""
    # HTML 엔티티 제거
    title = unescape(title)
    summary = unescape(summary)
    
    # 기본값
    company = "Unknown"
    etf_name = "ETF Registration"
    
    # 패턴 1: "회사명 - ETF 이름 - Form Type"
    match = re.search(r'^([^-–—]+?)\s*[-–—]\s*([^-–—]+?)(?:\s*[-–—]\s*(?:Form\s+)?[\w/]+)?$', title)
    if match:
        company = match.group(1).strip()
        potential_name = match.group(2).strip()
        
        # Form 타입 제거
        potential_name = re.sub(r'\(?\s*Form\s+[\w/]+\s*\)?$', '', potential_name).strip()
        potential_name = re.sub(r'^\s*Form\s+[\w/]+\s*', '', potential_name).strip()
        
        if potential_name and len(potential_name) > 3:
            etf_name = potential_name
    
    # 패턴 2: Summary에서 ETF 이름 찾기
    if etf_name == "ETF Registration" and summary:
        etf_match = re.search(r'([A-Za-z][A-Za-z\s&]+(?:ETF|Exchange[- ]Traded Fund?|Fund|Trust))', summary, re.IGNORECASE)
        if etf_match:
            etf_name = etf_match.group(1).strip()
    
    # 회사명 정리
    company = re.sub(r'\s+(?:Inc\.?|Corp\.?|LLC|LP|Ltd\.?|Trust|Funds?)\.?$', '', company, flags=re.IGNORECASE).strip()
    
    # ETF 이름 정리
    etf_name = re.sub(r'\s+', ' ', etf_name).strip()
    
    return company, etf_name

def format_etf_report(filings):
    """간결한 ETF 리포트 포맷"""
    korean_time = get_korean_time()
    yesterday, today = get_date_range()
    
    report = f"""📊 <b>SEC ETF 신규 상장신청</b>
━━━━━━━━━━━━━━━━━
📅 {yesterday} ~ {today} (미국)
🕐 {korean_time.strftime('%H:%M')} KST

"""
    
    if not filings:
        report += """⚠️ 신규 상장신청 없음

최근 2일간 ETF 신규 상장신청이 없습니다."""
    else:
        report += f"""🆕 <b>신규 {len(filings)}건</b>

"""
        # 날짜별 그룹화
        by_date = {}
        for filing in filings:
            date = filing['filing_date']
            if date not in by_date:
                by_date[date] = []
            by_date[date].append(filing)
        
        for date in sorted(by_date.keys(), reverse=True):
            if len(by_date) > 1:
                report += f"<b>[{date}]</b>\n"
            
            for filing in by_date[date]:
                # 표시할 이름 결정
                if filing['etf_name'] != "ETF Registration":
                    display = f"<b>{filing['company']}</b> - {filing['etf_name']}"
                else:
                    display = f"<b>{filing['company']}</b>"
                
                report += f"""• {display}
  {filing['filing_type']} | <a href="{filing['url']}">SEC Filing →</a>

"""
    
    report += f"""━━━━━━━━━━━━━━━━━
🔄 매일 오전 8시 발송"""
    
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
            "filings_detail": filings,  # 디버깅용
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
    """SEC 데이터 수집 테스트 (상세 정보)"""
    try:
        logger.info("SEC 데이터 테스트 시작...")
        
        # 각 Form별 상세 수집
        n1a = get_filings_by_form("N-1A")
        apos = get_filings_by_form("485APOS")
        bxt = get_filings_by_form("485BXT")
        
        return jsonify({
            "status": "success",
            "summary": {
                "N-1A": len(n1a),
                "485APOS": len(apos),
                "485BXT": len(bxt),
                "total": len(n1a) + len(apos) + len(bxt)
            },
            "details": {
                "N-1A": n1a[:3],  # 처음 3개만
                "485APOS": apos[:3],
                "485BXT": bxt[:3]
            },
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
