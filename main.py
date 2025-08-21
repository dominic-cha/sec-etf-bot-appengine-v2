import requests
import schedule
import time
import os
import threading
from datetime import datetime, timezone, timedelta
from flask import Flask

# Flask 앱 생성 (App Engine 요구사항)
app = Flask(__name__)

# 환경변수
BOT_TOKEN = os.getenv('BOT_TOKEN')
CHAT_ID = os.getenv('CHAT_ID')

# 한국 시간대
KST = timezone(timedelta(hours=9))

def get_korean_time():
    """현재 한국 시간 반환"""
    return datetime.now(KST)

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

def send_startup_message():
    """App Engine 시작 알림"""
    korean_time = get_korean_time()
    weekday_name = ['월', '화', '수', '목', '금', '토', '일'][korean_time.weekday()]
    
    startup_message = f"""🏗️ <b>Google App Engine 마이그레이션 완료!</b>

📅 {korean_time.strftime('%Y-%m-%d')} ({weekday_name}요일)
⏰ {korean_time.strftime('%H:%M:%S')} (KST)

<b>🌐 새로운 인프라:</b>
- Google App Engine (Flask 기반)
- 24시간 안정적 실행
- 영구 무료 서비스

<b>📊 환경변수 상태:</b>
- BOT_TOKEN: {'✅ 설정됨' if BOT_TOKEN else '❌ 미설정'}
- CHAT_ID: {'✅ 설정됨' if CHAT_ID else '❌ 미설정'}

<b>💎 마이그레이션 혜택:</b>
- Railway 비용 걱정 없음
- Google Cloud 인프라 활용
- 장기적 안정성 확보

Flask + App Engine 배포 성공! 🚀"""

    send_telegram_message(startup_message)

def run_test():
    """테스트 실행"""
    korean_time = get_korean_time()
    
    test_message = f"""🧪 <b>App Engine 테스트 성공!</b>

⏰ {korean_time.strftime('%H:%M:%S')} 테스트 실행
📅 {korean_time.strftime('%Y-%m-%d')}

🎉 Flask + 스케줄러 정상 작동!"""

    send_telegram_message(test_message)

def run_scheduler():
    """백그라운드에서 스케줄러 실행"""
    print("⏰ 백그라운드 스케줄러 시작...")
    
    # 스케줄 설정
    schedule.every().day.at("08:00").do(run_test)
    schedule.every().hour.at(":00").do(run_test)  # 테스트용
    
    while True:
        schedule.run_pending()
        time.sleep(60)

# Flask 라우트들
@app.route('/')
def hello():
    """메인 페이지"""
    korean_time = get_korean_time()
    return f"""
    <h1>🤖 SEC ETF 봇 - App Engine</h1>
    <p><strong>현재 시간:</strong> {korean_time.strftime('%Y-%m-%d %H:%M:%S')} (KST)</p>
    <p><strong>BOT_TOKEN:</strong> {'✅ 설정됨' if BOT_TOKEN else '❌ 미설정'}</p>
    <p><strong>CHAT_ID:</strong> {'✅ 설정됨' if CHAT_ID else '❌ 미설정'}</p>
    <p><strong>상태:</strong> 🟢 정상 작동 중</p>
    <hr>
    <p>🚀 Google App Engine에서 24시간 실행 중!</p>
    """

@app.route('/test')
def manual_test():
    """수동 테스트"""
    run_test()
    return "✅ 테스트 메시지 전송 완료!"

@app.route('/startup')
def send_startup():
    """시작 메시지 수동 전송"""
    send_startup_message()
    return "✅ 시작 메시지 전송 완료!"

@app.route('/health')
def health_check():
    """헬스체크"""
    return "OK"

# 앱 시작 시 실행
if __name__ == '__main__':
    print("🏗️ Google App Engine Flask 앱 시작!")
    print(f"📱 BOT_TOKEN: {'✅ 설정됨' if BOT_TOKEN else '❌ 미설정'}")
    print(f"💬 CHAT_ID: {'✅ 설정됨' if CHAT_ID else '❌ 미설정'}")
    
    # 시작 알림
    if BOT_TOKEN and CHAT_ID:
        send_startup_message()
    
    # 백그라운드 스케줄러 시작
    scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
    scheduler_thread.start()
    
    # Flask 앱 실행
    app.run(host='0.0.0.0', port=8080, debug=False)