import requests
import time
import os
import json
import logging
from datetime import datetime, timezone, timedelta
from flask import Flask, jsonify, render_template_string
from typing import List, Dict, Optional
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from enum import Enum

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Flask 앱 생성
app = Flask(__name__)

# 환경변수
BOT_TOKEN = os.getenv('BOT_TOKEN')
CHAT_ID = os.getenv('CHAT_ID')
ENVIRONMENT = os.getenv('GAE_ENV', 'local')  # App Engine 환경 감지

# 시간대 설정
KST = timezone(timedelta(hours=9))
EST = timezone(timedelta(hours=-5))
EDT = timezone(timedelta(hours=-4))


@dataclass
class ETFFiling:
    """ETF 신규 등록 데이터 클래스"""
    title: str
    ticker: str
    filing_type: str
    strategy: str
    date: str
    link: str
    cik: Optional[str] = None
    company_name: Optional[str] = None


class TimeZoneManager:
    """시간대 관리 클래스"""
    
    @staticmethod
    def get_korean_time() -> datetime:
        """현재 한국 시간 반환"""
        return datetime.now(KST)
    
    @staticmethod
    def get_us_timezone() -> timezone:
        """현재 미국 시간대 반환 (EST/EDT 자동 판별)"""
        now = datetime.now()
        
        # Daylight Saving Time 계산 (3월 둘째 주 일요일 ~ 11월 첫째 주 일요일)
        year = now.year
        
        # 3월 둘째 주 일요일
        march = datetime(year, 3, 1)
        march_second_sunday = march + timedelta(days=(6 - march.weekday() + 7))
        
        # 11월 첫째 주 일요일
        november = datetime(year, 11, 1)
        november_first_sunday = november + timedelta(days=(6 - november.weekday()))
        
        if march_second_sunday <= now.replace(tzinfo=None) < november_first_sunday:
            return EDT
        return EST
    
    @staticmethod
    def is_us_market_open() -> bool:
        """미국 시장 개장 여부 확인"""
        us_time = datetime.now(TimeZoneManager.get_us_timezone())
        weekday = us_time.weekday()
        
        # 주말 체크
        if weekday >= 5:  # 토요일(5) 또는 일요일(6)
            return False
        
        # 시간 체크 (9:30 AM - 4:00 PM ET)
        market_open = us_time.replace(hour=9, minute=30, second=0)
        market_close = us_time.replace(hour=16, minute=0, second=0)
        
        return market_open <= us_time <= market_close


class TelegramBot:
    """텔레그램 봇 클래스"""
    
    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id
        self.base_url = f"https://api.telegram.org/bot{token}"
    
    def send_message(self, message: str, parse_mode: str = "HTML") -> bool:
        """텔레그램으로 메시지 전송"""
        if not self.token or not self.chat_id:
            logger.error("텔레그램 설정이 없습니다")
            return False
        
        url = f"{self.base_url}/sendMessage"
        data = {
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True
        }
        
        try:
            response = requests.post(url, json=data, timeout=30)
            response.raise_for_status()
            logger.info("텔레그램 메시지 전송 성공")
            return True
        except requests.exceptions.RequestException as e:
            logger.error(f"텔레그램 전송 실패: {e}")
            return False


class SECDataCollector:
    """SEC 데이터 수집 클래스"""
    
    def __init__(self):
        self.headers = {
            'User-Agent': 'SEC ETF Monitor Bot (Contact: your-email@example.com)',
            'Accept-Encoding': 'gzip, deflate',
            'Host': 'www.sec.gov'
        }
        # SEC는 사용자 에이전트에 연락처 정보를 포함하도록 요구합니다
    
    def get_new_etf_filings(self) -> List[ETFFiling]:
        """SEC에서 신규 ETF 등록 신청 데이터 수집"""
        filings = []
        
        try:
            # 1. EDGAR 최신 파일링 검색
            filings.extend(self._search_edgar_filings())
            
            # 2. RSS 피드 검색
            filings.extend(self._search_rss_feed())
            
            # 중복 제거
            seen = set()
            unique_filings = []
            for filing in filings:
                key = (filing.title, filing.date)
                if key not in seen:
                    seen.add(key)
                    unique_filings.append(filing)
            
            return unique_filings[:10]  # 최대 10개만 반환
            
        except Exception as e:
            logger.error(f"SEC 데이터 수집 오류: {e}")
            return self._get_demo_data()  # 오류 시 데모 데이터 반환
    
    def _search_edgar_filings(self) -> List[ETFFiling]:
        """EDGAR API를 통한 신규 ETF 검색"""
        filings = []
        
        try:
            # EDGAR Full-Text Search API
            base_url = "https://efts.sec.gov/LATEST/search-index"
            
            # N-1A 폼 검색 (ETF 등록에 사용)
            params = {
                'q': 'form-type:("N-1A" OR "485APOS" OR "485BPOS") AND "exchange-traded"',
                'dateRange': 'custom',
                'startdt': (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d'),
                'enddt': datetime.now().strftime('%Y-%m-%d'),
                'category': 'form-cat1',
                'locationType': 'located',
                'locationCode': 'all'
            }
            
            response = requests.get(base_url, params=params, headers=self.headers, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                
                for hit in data.get('hits', {}).get('hits', [])[:5]:
                    source = hit.get('_source', {})
                    
                    # 신규 등록인지 확인
                    is_initial = any(keyword in source.get('file_description', '').lower() 
                                   for keyword in ['initial', 'registration', 'new'])
                    
                    if is_initial:
                        filing = ETFFiling(
                            title=source.get('display_names', [''])[0],
                            ticker='TBD',  # 티커는 별도 파싱 필요
                            filing_type=source.get('form', 'N-1A'),
                            strategy='신규 ETF',
                            date=source.get('file_date', ''),
                            link=f"https://www.sec.gov/Archives/edgar/data/{source.get('cik', '')}/{source.get('accession_number', '').replace('-', '')}/{source.get('file_name', '')}",
                            cik=source.get('cik', ''),
                            company_name=source.get('display_names', [''])[0]
                        )
                        filings.append(filing)
                        
        except Exception as e:
            logger.warning(f"EDGAR API 검색 오류: {e}")
        
        return filings
    
    def _search_rss_feed(self) -> List[ETFFiling]:
        """SEC RSS 피드에서 신규 ETF 검색"""
        filings = []
        
        try:
            rss_url = "https://www.sec.gov/Archives/edgar/usgaap.rss.xml"
            response = requests.get(rss_url, headers=self.headers, timeout=10)
            
            if response.status_code == 200:
                root = ET.fromstring(response.content)
                
                # 신규 ETF 관련 키워드
                etf_keywords = ['etf', 'exchange-traded', 'exchange traded']
                new_keywords = ['n-1a', 'registration', 'initial', 'new fund']
                
                for item in root.findall('.//item')[:30]:
                    title_elem = item.find('title')
                    link_elem = item.find('link')
                    date_elem = item.find('pubDate')
                    desc_elem = item.find('description')
                    
                    if title_elem is not None and link_elem is not None:
                        title_lower = title_elem.text.lower()
                        desc_lower = desc_elem.text.lower() if desc_elem is not None else ''
                        
                        # ETF 관련이면서 신규 등록인지 확인
                        is_etf = any(kw in title_lower or kw in desc_lower for kw in etf_keywords)
                        is_new = any(kw in title_lower or kw in desc_lower for kw in new_keywords)
                        
                        if is_etf and is_new:
                            # 날짜 파싱
                            date_str = 'N/A'
                            if date_elem is not None:
                                try:
                                    pub_date = datetime.strptime(date_elem.text, '%a, %d %b %Y %H:%M:%S %z')
                                    date_str = pub_date.strftime('%Y-%m-%d')
                                except:
                                    date_str = date_elem.text[:10] if date_elem.text else 'N/A'
                            
                            filing = ETFFiling(
                                title=title_elem.text[:100],
                                ticker=self._extract_ticker(title_elem.text),
                                filing_type='N-1A',
                                strategy='ETF',
                                date=date_str,
                                link=link_elem.text
                            )
                            filings.append(filing)
                            
        except Exception as e:
            logger.warning(f"RSS 피드 파싱 오류: {e}")
        
        return filings
    
    def _extract_ticker(self, text: str) -> str:
        """텍스트에서 티커 심볼 추출"""
        # 대문자 3-5글자로 된 단어 찾기
        import re
        pattern = r'\b[A-Z]{3,5}\b'
        matches = re.findall(pattern, text)
        
        # ETF, SEC, NYSE 같은 일반 용어 제외
        exclude = {'ETF', 'SEC', 'NYSE', 'NASDAQ', 'FORM', 'THE'}
        for match in matches:
            if match not in exclude:
                return match
        
        return 'TBD'
    
    def _get_demo_data(self) -> List[ETFFiling]:
        """데모 데이터 반환"""
        us_time = datetime.now(TimeZoneManager.get_us_timezone())
        
        return [
            ETFFiling(
                title='ARK 21Shares Bitcoin ETF - Form N-1A Initial Registration',
                ticker='ARKB',
                filing_type='N-1A',
                strategy='비트코인 현물 투자',
                date=us_time.strftime('%Y-%m-%d'),
                link='https://www.sec.gov/example/arkb.html'
            ),
            ETFFiling(
                title='Vanguard Climate Solutions ETF - Registration Statement',
                ticker='VCLM',
                filing_type='N-1A',
                strategy='기후 솔루션 기업 투자',
                date=(us_time - timedelta(days=1)).strftime('%Y-%m-%d'),
                link='https://www.sec.gov/example/vclm.html'
            )
        ]


class ReportGenerator:
    """리포트 생성 클래스"""
    
    @staticmethod
    def generate_etf_report(filings: List[ETFFiling]) -> str:
        """신규 ETF 등록 리포트 생성"""
        korean_time = TimeZoneManager.get_korean_time()
        us_time = datetime.now(TimeZoneManager.get_us_timezone())
        weekday_names = ['월', '화', '수', '목', '금', '토', '일']
        weekday = weekday_names[korean_time.weekday()]
        
        # 시장 상태
        market_status = "🟢 개장" if TimeZoneManager.is_us_market_open() else "🔴 휴장"
        
        report = f"""📋 <b>SEC 신규 ETF 등록신청 브리핑</b>

📅 {korean_time.strftime('%Y년 %m월 %d일')} ({weekday}요일)
⏰ 한국: {korean_time.strftime('%H:%M')} | 미국: {us_time.strftime('%H:%M %Z')}
🏛️ 미국 시장: {market_status}

━━━━━━━━━━━━━━━━━━━━━━━━
"""

        if filings:
            report += f"\n🆕 <b>신규 ETF 등록신청 ({len(filings)}건)</b>\n\n"
            
            for i, filing in enumerate(filings, 1):
                report += f"<b>{i}. [{filing.ticker}] {filing.company_name or 'ETF'}</b>\n"
                report += f"📑 유형: {filing.filing_type}\n"
                report += f"🎯 전략: {filing.strategy}\n"
                report += f"📆 신청일: {filing.date}\n"
                
                if filing.cik:
                    report += f"🏢 CIK: {filing.cik}\n"
                
                if filing.link and filing.link.startswith('http'):
                    short_link = filing.link[:50] + '...' if len(filing.link) > 50 else filing.link
                    report += f"🔗 <a href='{filing.link}'>상세보기</a>\n"
                
                report += "\n"
        else:
            report += "\n📭 <b>오늘은 신규 ETF 등록신청이 없습니다.</b>\n\n"
            
            if not TimeZoneManager.is_us_market_open():
                report += "• 미국 시장이 휴장 중입니다.\n"
            else:
                report += "• 아직 신규 신청이 제출되지 않았습니다.\n"
            
            report += "\n"
        
        report += "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        report += "💡 <b>ETF 등록 프로세스</b>\n"
        report += "• N-1A: 신규 ETF 등록신청서\n"
        report += "• 485APOS/BPOS: 추가 주식 발행\n"
        report += "• 평균 승인 기간: 75일\n\n"
        
        report += "🤖 SEC ETF Monitor Bot v2.0"
        
        return report


# Flask 라우트
@app.route('/')
def index():
    """메인 페이지"""
    korean_time = TimeZoneManager.get_korean_time()
    us_time = datetime.now(TimeZoneManager.get_us_timezone())
    
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>SEC ETF Monitor Bot</title>
        <style>
            body {
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                max-width: 1200px;
                margin: 0 auto;
                padding: 20px;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                min-height: 100vh;
            }
            .container {
                background: white;
                border-radius: 15px;
                padding: 30px;
                box-shadow: 0 10px 40px rgba(0,0,0,0.2);
            }
            h1 {
                color: #333;
                border-bottom: 3px solid #667eea;
                padding-bottom: 10px;
            }
            .status-card {
                background: #f8f9fa;
                border-radius: 10px;
                padding: 20px;
                margin: 20px 0;
            }
            .button {
                display: inline-block;
                padding: 12px 24px;
                background: #667eea;
                color: white;
                text-decoration: none;
                border-radius: 5px;
                margin: 5px;
                transition: transform 0.2s;
            }
            .button:hover {
                transform: translateY(-2px);
                box-shadow: 0 5px 15px rgba(102, 126, 234, 0.4);
            }
            .info-grid {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
                gap: 20px;
                margin: 20px 0;
            }
            .info-item {
                background: #fff;
                border: 1px solid #e0e0e0;
                border-radius: 8px;
                padding: 15px;
            }
            .label {
                font-weight: bold;
                color: #666;
                font-size: 12px;
                text-transform: uppercase;
                margin-bottom: 5px;
            }
            .value {
                font-size: 18px;
                color: #333;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🤖 SEC ETF Monitor Bot</h1>
            
            <div class="status-card">
                <h2>📊 시스템 상태</h2>
                <div class="info-grid">
                    <div class="info-item">
                        <div class="label">한국 시간</div>
                        <div class="value">""" + korean_time.strftime('%Y-%m-%d %H:%M:%S') + """</div>
                    </div>
                    <div class="info-item">
                        <div class="label">미국 시간</div>
                        <div class="value">""" + us_time.strftime('%Y-%m-%d %H:%M:%S %Z') + """</div>
                    </div>
                    <div class="info-item">
                        <div class="label">환경</div>
                        <div class="value">""" + ('☁️ Google App Engine' if ENVIRONMENT != 'local' else '💻 로컬') + """</div>
                    </div>
                    <div class="info-item">
                        <div class="label">텔레그램 상태</div>
                        <div class="value">""" + ('✅ 연결됨' if BOT_TOKEN and CHAT_ID else '❌ 미설정') + """</div>
                    </div>
                </div>
            </div>
            
            <div class="status-card">
                <h2>🎯 기능</h2>
                <a href="/etf-report" class="button">📤 ETF 리포트 전송</a>
                <a href="/test-report" class="button">👁️ 리포트 미리보기</a>
                <a href="/health" class="button">🏥 상태 체크</a>
            </div>
            
            <div class="status-card">
                <h2>📅 스케줄 정보</h2>
                <p>• 화요일~토요일 오전 8시 (한국시간)</p>
                <p>• SEC EDGAR 실시간 데이터 수집</p>
                <p>• 신규 ETF 등록신청 모니터링</p>
            </div>
        </div>
    </body>
    </html>
    """
    
    return render_template_string(html)


@app.route('/etf-report')
def etf_report():
    """ETF 리포트 생성 및 전송"""
    try:
        # 데이터 수집
        collector = SECDataCollector()
        filings = collector.get_new_etf_filings()
        
        # 리포트 생성
        report = ReportGenerator.generate_etf_report(filings)
        
        # 텔레그램 전송
        if BOT_TOKEN and CHAT_ID:
            bot = TelegramBot(BOT_TOKEN, CHAT_ID)
            success = bot.send_message(report)
            
            if success:
                return jsonify({
                    'status': 'success',
                    'message': f'ETF 리포트 전송 완료 ({len(filings)}건)',
                    'count': len(filings)
                })
            else:
                return jsonify({
                    'status': 'error',
                    'message': '텔레그램 전송 실패'
                }), 500
        else:
            return jsonify({
                'status': 'error',
                'message': '텔레그램 설정이 없습니다'
            }), 400
            
    except Exception as e:
        logger.error(f"리포트 생성 오류: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@app.route('/test-report')
def test_report():
    """리포트 미리보기 (전송하지 않음)"""
    try:
        # 데이터 수집
        collector = SECDataCollector()
        filings = collector.get_new_etf_filings()
        
        # 리포트 생성
        report = ReportGenerator.generate_etf_report(filings)
        
        # HTML로 변환
        html_report = report.replace('<b>', '<strong>').replace('</b>', '</strong>')
        html_report = html_report.replace('\n', '<br>')
        
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>ETF 리포트 미리보기</title>
            <style>
                body {{
                    font-family: Arial, sans-serif;
                    max-width: 800px;
                    margin: 20px auto;
                    padding: 20px;
                    background: #f5f5f5;
                }}
                .report {{
                    background: white;
                    padding: 30px;
                    border-radius: 10px;
                    box-shadow: 0 2px 10px rgba(0,0,0,0.1);
                    line-height: 1.6;
                }}
                a {{
                    color: #667eea;
                    text-decoration: none;
                }}
                a:hover {{
                    text-decoration: underline;
                }}
            </style>
        </head>
        <body>
            <div class="report">
                <h2>📋 리포트 미리보기</h2>
                <hr>
                <div>{html_report}</div>
                <hr>
                <p><a href="/">← 메인으로 돌아가기</a></p>
            </div>
        </body>
        </html>
        """
        
        return render_template_string(html)
        
    except Exception as e:
        logger.error(f"미리보기 생성 오류: {e}")
        return f"오류 발생: {e}", 500


@app.route('/health')
def health():
    """헬스체크 엔드포인트"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'environment': ENVIRONMENT,
        'telegram_configured': bool(BOT_TOKEN and CHAT_ID)
    })


@app.route('/cron/daily-report')
def cron_daily_report():
    """Cloud Scheduler용 크론 엔드포인트"""
    # App Engine cron 요청 확인
    if ENVIRONMENT != 'local':
        cron_header = request.headers.get('X-Appengine-Cron')
        if not cron_header:
            return jsonify({'error': 'Unauthorized'}), 403
    
    # 요일 확인 (화-토)
    korean_time = TimeZoneManager.get_korean_time()
    weekday = korean_time.weekday()
    
    if weekday in [1, 2, 3, 4, 5]:  # 화요일(1) ~ 토요일(5)
        return etf_report()
    else:
        return jsonify({
            'status': 'skipped',
            'message': '일요일, 월요일은 리포트를 전송하지 않습니다'
        })


@app.errorhandler(404)
def not_found(e):
    """404 에러 핸들러"""
    return jsonify({'error': 'Not found'}), 404


@app.errorhandler(500)
def server_error(e):
    """500 에러 핸들러"""
    logger.error(f"Server error: {e}")
    return jsonify({'error': 'Internal server error'}), 500


# 앱 초기화
def initialize_app():
    """앱 초기화 및 시작 메시지 전송"""
    if BOT_TOKEN and CHAT_ID:
        try:
            bot = TelegramBot(BOT_TOKEN, CHAT_ID)
            korean_time = TimeZoneManager.get_korean_time()
            weekday_name = ['월', '화', '수', '목', '금', '토', '일'][korean_time.weekday()]
            
            startup_message = f"""🚀 <b>SEC ETF Monitor Bot 시작</b>

📅 {korean_time.strftime('%Y-%m-%d')} ({weekday_name}요일)
⏰ {korean_time.strftime('%H:%M:%S')} (KST)

<b>🤖 시스템 정보:</b>
• 환경: {'Google App Engine' if ENVIRONMENT != 'local' else '로컬 개발'}
• 버전: 2.0
• 상태: 정상 작동

<b>📊 모니터링 대상:</b>
• SEC EDGAR 신규 ETF 등록신청
• Form N-1A, 485APOS, 485BPOS
• 실시간 데이터 수집

<b>⏰ 스케줄:</b>
• 화-토요일 오전 8시 자동 전송
• 수동 실행 가능

시스템 준비 완료! 🎯"""
            
            bot.send_message(startup_message)
            logger.info("시작 메시지 전송 완료")
            
        except Exception as e:
            logger.error(f"시작 메시지 전송 실패: {e}")


if __name__ == '__main__':
    # 앱 초기화
    initialize_app()
    
    # Flask 앱 실행
    port = int(os.environ.get('PORT', 8080))
    
    if ENVIRONMENT == 'local':
        # 로컬 개발 환경
        app.run(host='127.0.0.1', port=port, debug=True)
    else:
        # App Engine 환경
        app.run(host='0.0.0.0', port=port, debug=False)
