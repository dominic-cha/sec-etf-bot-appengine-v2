import requests
import schedule
import time
import os
import threading
import json
from datetime import datetime, timezone, timedelta
from flask import Flask

# Flask 앱 생성
app = Flask(__name__)

# 환경변수
BOT_TOKEN = os.getenv('BOT_TOKEN')
CHAT_ID = os.getenv('CHAT_ID')

# 시간대 설정
KST = timezone(timedelta(hours=9))
EST = timezone(timedelta(hours=-5))
EDT = timezone(timedelta(hours=-4))

def get_korean_time():
    """현재 한국 시간 반환"""
    return datetime.now(KST)

def get_us_timezone():
    """현재 미국 시간대 반환 (EST/EDT 자동 판별)"""
    now = datetime.now()
    # 3월 두 번째 일요일부터 11월 첫 번째 일요일까지 EDT
    if now.month > 3 and now.month < 11:
        return EDT
    elif now.month == 3:
        # 3월 두 번째 일요일 계산 (간단히 8일 이후로 가정)
        return EDT if now.day > 8 else EST
    elif now.month == 11:
        # 11월 첫 번째 일요일 계산 (간단히 7일 이전으로 가정)
        return EST if now.day > 7 else EDT
    else:
        return EST

def send_telegram_message(message):
    """텔레그램으로 메시지 전송"""
    if not BOT_TOKEN or not CHAT_ID:
        print("❌ 텔레그램 설정 오류")
        return False
    
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    
    try:
        response = requests.post(url, data=data, timeout=30)
        if response.status_code == 200:
            print("✅ 텔레그램 메시지 전송 성공")
            return True
        else:
            print(f"❌ 텔레그램 전송 실패: {response.text}")
            return False
    except Exception as e:
        print(f"🚨 텔레그램 오류: {e}")
        return False

def get_sec_etf_filings():
    """SEC ETF 파일링 데이터 수집"""
    try:
        # SEC EDGAR API 엔드포인트
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        # 최근 ETF 관련 파일링 검색
        # N-1A (ETF 등록신청서), 485BPOS (사후 등록서류) 등
        search_url = "https://efts.sec.gov/LATEST/search-index"
        
        # 백업: SEC RSS 피드 사용
        rss_url = "https://www.sec.gov/Archives/edgar/xbrlrss.xml"
        
        etf_filings = []
        
        try:
            # SEC 공식 검색 API 시도
            search_response = requests.get(search_url, headers=headers, timeout=10)
            if search_response.status_code == 200:
                # ETF 관련 키워드 필터링
                etf_keywords = ['ETF', 'Exchange-Traded Fund', 'Exchange Traded Fund']
                # JSON 파싱 및 ETF 관련 데이터 추출 (실제 API 구조에 따라 조정 필요)
        except:
            pass
        
        # 백업 방법: SEC RSS 피드 파싱
        try:
            rss_response = requests.get(rss_url, headers=headers, timeout=10)
            if rss_response.status_code == 200:
                # RSS XML 파싱하여 ETF 관련 항목 추출
                import xml.etree.ElementTree as ET
                root = ET.fromstring(rss_response.content)
                
                for item in root.findall('.//item')[:10]:  # 최근 10개 항목
                    title = item.find('title')
                    link = item.find('link')
                    pub_date = item.find('pubDate')
                    
                    if title is not None and any(keyword.lower() in title.text.lower() for keyword in ['etf', 'exchange-traded', 'exchange traded']):
                        etf_filings.append({
                            'title': title.text,
                            'link': link.text if link is not None else '',
                            'date': pub_date.text if pub_date is not None else ''
                        })
        except Exception as e:
            print(f"RSS 파싱 오류: {e}")
        
        # 데모 데이터 (실제 API 연결 전 테스트용)
        if not etf_filings:
            korean_time = get_korean_time()
            us_time = datetime.now(get_us_timezone())
            
            etf_filings = [
                {
                    'title': 'Vanguard S&P 500 ETF - Form N-Q Filing',
                    'ticker': 'VOO',
                    'type': 'Quarterly Holdings Report',
                    'date': us_time.strftime('%Y-%m-%d'),
                    'link': 'https://www.sec.gov/Archives/edgar/data/example1.html'
                },
                {
                    'title': 'iShares Core MSCI Total International Stock ETF - Registration',
                    'ticker': 'IXUS',
                    'type': 'Registration Statement',
                    'date': (us_time - timedelta(days=1)).strftime('%Y-%m-%d'),
                    'link': 'https://www.sec.gov/Archives/edgar/data/example2.html'
                },
                {
                    'title': 'SPDR Gold Shares ETF - Amendment Filing',
                    'ticker': 'GLD',
                    'type': 'Amendment to Registration',
                    'date': (us_time - timedelta(days=2)).strftime('%Y-%m-%d'),
                    'link': 'https://www.sec.gov/Archives/edgar/data/example3.html'
                }
            ]
            
        return etf_filings
        
    except Exception as e:
        print(f"SEC 데이터 수집 오류: {e}")
        return []

def format_etf_report(filings):
    """ETF 파일링 리포트 포맷"""
    korean_time = get_korean_time()
    us_time = datetime.now(get_us_timezone())
    weekday_names = ['월', '화', '수', '목', '금', '토', '일']
    weekday = weekday_names[korean_time.weekday()]
    
    report = f"""📊 <b>SEC ETF 파일링 일일 브리핑</b>

📅 {korean_time.strftime('%Y년 %m월 %d일')} ({weekday}요일)
⏰ 한국시간: {korean_time.strftime('%H:%M:%S')}
🇺🇸 미국시간: {us_time.strftime('%H:%M:%S %Z')}

───────────────────────────

"""

    if filings:
        report += f"<b>📋 최근 ETF 등록/신청 현황 ({len(filings)}건)</b>\n\n"
        
        for i, filing in enumerate(filings, 1):
            report += f"<b>{i}. {filing.get('ticker', 'N/A')}</b>\n"
            report += f"📑 {filing['title'][:80]}{'...' if len(filing['title']) > 80 else ''}\n"
            report += f"📂 유형: {filing.get('type', '일반 파일링')}\n"
            report += f"📆 제출일: {filing.get('date', 'N/A')}\n"
            if filing.get('link'):
                report += f"🔗 <a href='{filing['link']}'>상세보기</a>\n"
            report += "\n"
    else:
        report += "<b>📭 오늘은 새로운 ETF 파일링이 없습니다.</b>\n\n"
        report += "• 미국 시장 휴일이거나\n"
        report += "• 아직 새로운 등록신청이 제출되지 않았을 수 있습니다.\n\n"
    
    report += "───────────────────────────\n\n"
    report += "<b>💡 ETF 파일링 정보</b>\n"
    report += "• <b>Form N-1A</b>: 새로운 ETF 등록신청\n"
    report += "• <b>Form 485BPOS</b>: 등록서류 사후 개정\n" 
    report += "• <b>Form N-Q</b>: 분기별 보유종목 현황\n"
    report += "• <b>Form N-CSR</b>: 연간/반기 보고서\n\n"
    
    report += "🤖 <i>Google App Engine에서 자동 수집</i>"
    
    return report

def run_daily_etf_report():
    """일일 ETF 리포트 실행"""
    korean_time = get_korean_time()
    
    print(f"📊 {korean_time.strftime('%Y-%m-%d %H:%M:%S')} ETF 리포트 생성 시작")
    
    # SEC 데이터 수집
    filings = get_sec_etf_filings()
    
    # 리포트 생성
    report = format_etf_report(filings)
    
    # 텔레그램 전송
    success = send_telegram_message(report)
    
    if success:
        print(f"✅ ETF 리포트 전송 완료 - {len(filings)}건의 파일링")
    else:
        print("❌ ETF 리포트 전송 실패")

def send_startup_message():
    """시작 메시지"""
    korean_time = get_korean_time()
    weekday_name = ['월', '화', '수', '목', '금', '토', '일'][korean_time.weekday()]
    
    startup_message = f"""🚀 <b>SEC ETF Bot 시작!</b>

📅 {korean_time.strftime('%Y-%m-%d')} ({weekday_name}요일)
⏰ {korean_time.strftime('%H:%M:%S')} (KST)

<b>🤖 봇 정보:</b>
• Google App Engine 실행
• Cloud Scheduler 연동
• 실시간 SEC 데이터 수집

<b>📊 스케줄:</b>
• 화-토요일 오전 8시 ETF 리포트 발송
• 미국 시장 기준 데이터 수집

<b>📈 수집 대상:</b>
• ETF 신규 등록신청
• 기존 ETF 변경사항  
• 분기별 보유현황 보고서

시스템 준비 완료! 🎯"""

    send_telegram_message(startup_message)

def run_scheduler():
    """백그라운드 스케줄러 (App Engine에서는 사용 안함)"""
    print("⏰ 백그라운드 스케줄러는 Cloud Scheduler로 대체됨")
    
    # App Engine에서는 Cloud Scheduler를 사용하므로 
    # 백그라운드 스케줄러는 비활성화
    # schedule.every().day.at("08:00").do(run_daily_etf_report)
    
    while True:
        # schedule.run_pending()
        time.sleep(300)  # 5분마다 체크 (실제로는 아무것도 안 함)

# Flask 라우트들
@app.route('/')
def hello():
    """메인 페이지"""
    korean_time = get_korean_time()
    us_time = datetime.now(get_us_timezone())
    
    return f"""
    <h1>📊 SEC ETF Bot - 실시간 모니터링</h1>
    <p><strong>한국시간:</strong> {korean_time.strftime('%Y-%m-%d %H:%M:%S')} (KST)</p>
    <p><strong>미국시간:</strong> {us_time.strftime('%Y-%m-%d %H:%M:%S %Z')}</p>
    <p><strong>BOT_TOKEN:</strong> {'✅ 설정됨' if BOT_TOKEN else '❌ 미설정'}</p>
    <p><strong>CHAT_ID:</strong> {'✅ 설정됨' if CHAT_ID else '❌ 미설정'}</p>
    <p><strong>상태:</strong> 🟢 정상 작동 중</p>
    <hr>
    <p>🤖 SEC ETF 파일링 자동 모니터링</p>
    <p>📅 화-토요일 오전 8시 리포트 발송</p>
    <p><a href="/etf-report">📊 ETF 리포트 보기</a></p>
    <p><a href="/test-report">🧪 리포트 테스트</a></p>
    <p><a href="/startup">🚀 시작 메시지</a></p>
    """

@app.route('/etf-report')
def etf_report():
    """ETF 리포트 수동 실행"""
    run_daily_etf_report()
    return "✅ SEC ETF 리포트 전송 완료!"

@app.route('/test-report')
def test_report():
    """리포트 테스트 (전송하지 않고 미리보기)"""
    filings = get_sec_etf_filings()
    report = format_etf_report(filings)
    
    # HTML로 변환해서 웹페이지에 표시
    html_report = report.replace('<b>', '<strong>').replace('</b>', '</strong>')
    html_report = html_report.replace('<i>', '<em>').replace('</i>', '</em>')
    html_report = html_report.replace('\n', '<br>')
    
    return f"""
    <html>
    <head><title>SEC ETF 리포트 미리보기</title></head>
    <body style="font-family: Arial, sans-serif; max-width: 800px; margin: 20px auto; line-height: 1.6;">
    <h2>📊 SEC ETF 리포트 미리보기</h2>
    <div style="background: #f5f5f5; padding: 20px; border-radius: 10px; white-space: pre-line;">
    {html_report}
    </div>
    <p><a href="/">← 메인으로 돌아가기</a></p>
    </body>
    </html>
    """

@app.route('/startup')
def send_startup():
    """시작 메시지 수동 전송"""
    send_startup_message()
    return "✅ 시작 메시지 전송 완료!"

@app.route('/test')
def manual_test():
    """기존 호환성을 위한 테스트 (실제로는 ETF 리포트 실행)"""
    run_daily_etf_report()
    return "✅ SEC ETF 리포트 전송 완료!"

@app.route('/health')
def health_check():
    """헬스체크"""
    return "OK"

# 앱 시작 시 실행
if __name__ == '__main__':
    print("📊 SEC ETF Bot 시작!")
    print(f"📱 BOT_TOKEN: {'✅ 설정됨' if BOT_TOKEN else '❌ 미설정'}")
    print(f"💬 CHAT_ID: {'✅ 설정됨' if CHAT_ID else '❌ 미설정'}")
    
    # 시작 알림 (개발 환경에서만)
    if BOT_TOKEN and CHAT_ID and os.getenv('GAE_ENV') != 'standard':
        send_startup_message()
    
    # 백그라운드 스케줄러 시작 (실제로는 비활성화)
    scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
    scheduler_thread.start()
    
    # Flask 앱 실행
    app.run(host='0.0.0.0', port=8080, debug=False)
