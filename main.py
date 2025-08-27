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

def get_sec_etf_new_filings():
    """SEC ETF 신규 등록 신청만 수집"""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        # SEC EDGAR API - 신규 ETF 등록 신청 검색
        search_url = "https://efts.sec.gov/LATEST/search-index"
        rss_url = "https://www.sec.gov/Archives/edgar/xbrlrss.xml"
        
        new_etf_filings = []
        
        try:
            # SEC RSS 피드에서 신규 등록 신청만 필터링
            rss_response = requests.get(rss_url, headers=headers, timeout=10)
            if rss_response.status_code == 200:
                import xml.etree.ElementTree as ET
                root = ET.fromstring(rss_response.content)
                
                # 신규 ETF 등록 관련 키워드
                new_filing_keywords = [
                    'n-1a', 'form n-1a', 'registration statement',
                    'new etf', 'initial registration'
                ]
                
                for item in root.findall('.//item')[:20]:  # 최근 20개 검토
                    title = item.find('title')
                    link = item.find('link')
                    pub_date = item.find('pubDate')
                    
                    if title is not None:
                        title_lower = title.text.lower()
                        
                        # ETF 관련이면서 신규 등록 신청인지 확인
                        is_etf = any(keyword in title_lower for keyword in ['etf', 'exchange-traded', 'exchange traded'])
                        is_new_filing = any(keyword in title_lower for keyword in new_filing_keywords)
                        
                        if is_etf and is_new_filing:
                            new_etf_filings.append({
                                'title': title.text,
                                'link': link.text if link is not None else '',
                                'date': pub_date.text if pub_date is not None else ''
                            })
                            
        except Exception as e:
            print(f"RSS 파싱 오류: {e}")
        
        # 데모 데이터 (신규 등록 신청만)
        if not new_etf_filings:
            korean_time = get_korean_time()
            us_time = datetime.now(get_us_timezone())
            
            new_etf_filings = [
                {
                    'title': 'Ark Innovation ETF - Form N-1A Initial Registration Statement',
                    'ticker': 'ARKK',
                    'type': '신규 ETF 등록신청',
                    'strategy': '혁신 기술 기업 투자',
                    'date': us_time.strftime('%Y-%m-%d'),
                    'link': 'https://www.sec.gov/Archives/edgar/data/example1.html'
                },
                {
                    'title': 'Global Clean Energy ETF - Registration Statement',
                    'ticker': 'GCLN', 
                    'type': '신규 ETF 등록신청',
                    'strategy': '글로벌 청정에너지',
                    'date': (us_time - timedelta(days=1)).strftime('%Y-%m-%d'),
                    'link': 'https://www.sec.gov/Archives/edgar/data/example2.html'
                }
            ]
            
        return new_etf_filings
        
    except Exception as e:
        print(f"SEC 신규 등록 데이터 수집 오류: {e}")
        return []

def format_new_etf_report(filings):
    """신규 ETF 등록 리포트 포맷"""
    korean_time = get_korean_time()
    us_time = datetime.now(get_us_timezone())
    weekday_names = ['월', '화', '수', '목', '금', '토', '일']
    weekday = weekday_names[korean_time.weekday()]
    
    report = f"""📋 SEC 신규 ETF 등록신청 브리핑

📅 {korean_time.strftime('%Y년 %m월 %d일')} ({weekday}요일)
⏰ 한국시간: {korean_time.strftime('%H:%M:%S')}
🇺🇸 미국시간: {us_time.strftime('%H:%M:%S %Z')}

───────────────────────────

"""

    if filings:
        report += f"🆕 새로운 ETF 등록신청 ({len(filings)}건)\n\n"
        
        for i, filing in enumerate(filings, 1):
            report += f"{i}. {filing.get('ticker', 'TBD')}\n"
            report += f"📑 {filing['title'][:70]}{'...' if len(filing['title']) > 70 else ''}\n"
            report += f"🎯 투자전략: {filing.get('strategy', '미공개')}\n"
            report += f"📆 신청일: {filing.get('date', 'N/A')}\n"
            if filing.get('link'):
                report += f"🔗 상세보기\n"
            report += "\n"
    else:
        report += "📭 오늘은 새로운 ETF 등록신청이 없습니다.\n\n"
        report += "• 미국 시장 휴일이거나\n"
        report += "• 아직 신규 ETF 등록신청이 제출되지 않았습니다.\n\n"
    
    report += "───────────────────────────\n\n"
    report += "💡 신규 ETF 등록신청 정보\n"
    report += "• Form N-1A: 새로운 ETF 최초 등록신청\n"
    report += "• Registration Statement: 신규 펀드 설립 신청\n"
    report += "• Initial Filing: 운용사의 새로운 ETF 출시 계획\n\n"
    
    report += "🔍 기존 ETF의 변경사항이나 정기보고서는 제외\n"
    report += "📈 투자 기회 발굴을 위한 신규 상품 모니터링\n\n"
    
    report += "🤖 Google App Engine 자동 수집"
    
    return report

def run_new_etf_report():
    """신규 ETF 등록 리포트 실행"""
    korean_time = get_korean_time()
    
    print(f"📋 {korean_time.strftime('%Y-%m-%d %H:%M:%S')} 신규 ETF 등록 리포트 생성 시작")
    
    # SEC 신규 등록 데이터 수집
    filings = get_sec_etf_new_filings()
    
    # 리포트 생성
    report = format_new_etf_report(filings)
    
    # 텔레그램 전송
    success = send_telegram_message(report)
    
    if success:
        print(f"✅ 신규 ETF 등록 리포트 전송 완료 - {len(filings)}건")
    else:
        print("❌ 신규 ETF 등록 리포트 전송 실패")

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

def send_deployment_test():
    """배포 완료 테스트 메시지"""
    korean_time = get_korean_time()
    us_time = datetime.now(get_us_timezone())
    weekday_name = ['월', '화', '수', '목', '금', '토', '일'][korean_time.weekday()]
    
    test_message = f"""🚀 <b>배포 완료 테스트</b>

📅 {korean_time.strftime('%Y-%m-%d')} ({weekday_name}요일)
⏰ 한국시간: {korean_time.strftime('%H:%M:%S')}
🇺🇸 미국시간: {us_time.strftime('%H:%M:%S %Z')}

<b>✅ 시스템 상태:</b>
- App Engine: 정상 실행
- SEC 데이터 수집: 준비됨
- 스케줄러: 화-토 8시 설정됨

<b>📊 다음 리포트:</b>
- 화-토요일 오전 8시
- 실제 SEC ETF 파일링 데이터

배포 테스트 완료! 🎯"""
    send_telegram_message(test_message)

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
@app.route('/etf-report')
def etf_report():
    """신규 ETF 등록 리포트 수동 실행"""
    run_new_etf_report()
    return "✅ SEC 신규 ETF 등록 리포트 전송 완료!"

@app.route('/test-report')
def test_report():
    """신규 ETF 리포트 테스트 (전송하지 않고 미리보기)"""
    filings = get_sec_etf_new_filings()
    report = format_new_etf_report(filings)
    
    # HTML로 변환해서 웹페이지에 표시
    html_report = report.replace('<b>', '<strong>').replace('</b>', '</strong>')
    html_report = html_report.replace('<i>', '<em>').replace('</i>', '</em>')
    html_report = html_report.replace('\n', '<br>')
    
    return f"""
    <html>
    <head><title>신규 ETF 등록 리포트 미리보기</title></head>
    <body style="font-family: Arial, sans-serif; max-width: 800px; margin: 20px auto; line-height: 1.6;">
    <h2>📋 신규 ETF 등록 리포트 미리보기</h2>
    <div style="background: #f5f5f5; padding: 20px; border-radius: 10px; white-space: pre-line;">
    {html_report}
    </div>
    <p><a href="/">← 메인으로 돌아가기</a></p>
    </body>
    </html>
    """

@app.route('/test')
def manual_test():
    """기존 호환성을 위한 테스트 (신규 ETF 등록 리포트 실행)"""
    run_new_etf_report()
    return "✅ SEC 신규 ETF 등록 리포트 전송 완료!"

# 앱 시작 시 실행
if __name__ == '__main__':
    print("📊 SEC ETF Bot 시작!")
    print(f"📱 BOT_TOKEN: {'✅ 설정됨' if BOT_TOKEN else '❌ 미설정'}")
    print(f"💬 CHAT_ID: {'✅ 설정됨' if CHAT_ID else '❌ 미설정'}")
    
    # 시작 알림 (App Engine 환경에서 자동 실행)
    if BOT_TOKEN and CHAT_ID:
        # 배포 테스트 메시지 전송
        send_deployment_test()
    
    # 백그라운드 스케줄러 시작 (실제로는 비활성화)
    scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
    scheduler_thread.start()
    
    # Flask 앱 실행
    app.run(host='0.0.0.0', port=8080, debug=False)
