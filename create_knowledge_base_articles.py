"""
Script to create Knowledge Base articles for Capulse
"""
from app import app, db
from models import BlogPost
from datetime import datetime

def create_articles():
    with app.app_context():
        articles = [
            {
                'title': 'Day Trading: A Comprehensive Guide for Indian Markets',
                'slug': 'day-trading-comprehensive-guide-indian-markets',
                'category': 'Trading Strategies',
                'tags': 'day trading, intraday, NSE, BSE, trading strategies',
                'excerpt': 'Master the art of day trading in Indian stock markets with our complete guide covering strategies, risk management, and essential tools for intraday success.',
                'content': '''
<h2>What is Day Trading?</h2>
<p>Day trading, also known as intraday trading, is a trading strategy where you buy and sell financial instruments within the same trading day. All positions are closed before the market closes, ensuring you don't carry overnight risk.</p>

<h2>Key Characteristics of Day Trading</h2>
<ul>
<li><strong>No Overnight Positions:</strong> All trades are squared off before market close (3:30 PM for NSE/BSE)</li>
<li><strong>High Frequency:</strong> Multiple trades executed throughout the day</li>
<li><strong>Leverage Available:</strong> Brokers offer intraday margin (up to 5-20x leverage)</li>
<li><strong>Quick Profits/Losses:</strong> Results are known by end of day</li>
</ul>

<h2>Popular Day Trading Strategies</h2>

<h3>1. Scalping</h3>
<p>Making numerous small profits on minor price changes. Traders hold positions for seconds to minutes, aiming for 0.1-0.5% gains per trade.</p>

<h3>2. Momentum Trading</h3>
<p>Identifying stocks with strong price movements and riding the trend. Look for stocks with 2%+ movement in first 30 minutes of trading.</p>

<h3>3. Gap and Go Strategy</h3>
<p>Trading stocks that open with significant gaps from previous close. Focus on gap-ups/gap-downs with volume confirmation.</p>

<h3>4. Range Trading</h3>
<p>Identifying support and resistance levels and trading within that range. Buy at support, sell at resistance.</p>

<h2>Essential Tools for Day Traders</h2>
<ul>
<li><strong>Real-Time Charts:</strong> 1-min, 5-min, 15-min candlestick charts</li>
<li><strong>Level 2 Data:</strong> Market depth and order flow</li>
<li><strong>News Feed:</strong> Real-time market news and corporate announcements</li>
<li><strong>Scanner/Screener:</strong> To find high-volume breakout stocks</li>
<li><strong>Fast Internet:</strong> Crucial for order execution</li>
</ul>

<h2>Risk Management Rules</h2>
<ol>
<li><strong>2% Rule:</strong> Never risk more than 2% of capital on a single trade</li>
<li><strong>Stop Loss:</strong> Always use stop-loss orders (typically 0.5-1% for day trades)</li>
<li><strong>Position Sizing:</strong> Calculate proper lot sizes based on risk</li>
<li><strong>Daily Loss Limit:</strong> Stop trading if you lose 6% of capital in a day</li>
<li><strong>Avoid Revenge Trading:</strong> Never chase losses emotionally</li>
</ol>

<h2>Best Stocks for Day Trading in India</h2>
<p>Focus on high liquidity stocks with daily volume >10 lakh shares:</p>
<ul>
<li>Large Cap: Reliance, TCS, Infosys, HDFC Bank, ICICI Bank</li>
<li>Mid Cap: Tata Motors, Vedanta, SAIL, Adani Enterprises</li>
<li>Index ETFs: Nifty BeES, Bank BeES for safer plays</li>
</ul>

<h2>Tax Implications</h2>
<p><strong>Important:</strong> Intraday gains are treated as speculative business income and taxed according to your income tax slab (up to 30% + cess). Maintain proper trading records for tax filing.</p>

<h2>Common Mistakes to Avoid</h2>
<ul>
<li>Trading without a plan or strategy</li>
<li>Overtrading to recover losses</li>
<li>Ignoring transaction costs (brokerage, STT, GST)</li>
<li>Using full margin without proper risk management</li>
<li>Trading during high volatility without experience</li>
</ul>

<h2>Getting Started</h2>
<p>1. Paper trade for at least 1-2 months<br>
2. Start with small capital (₹50,000-1 lakh)<br>
3. Trade only 2-3 stocks initially<br>
4. Gradually increase as you gain experience<br>
5. Keep a trading journal to track performance</p>

<p class="alert alert-warning"><strong>Risk Warning:</strong> Day trading is highly risky and not suitable for everyone. 90% of day traders lose money. Only trade with money you can afford to lose.</p>
                ''',
                'author_name': 'Capulse Research Team',
                'is_featured': True
            },
            {
                'title': 'Swing Trading: Capturing Multi-Day Price Movements',
                'slug': 'swing-trading-capturing-multi-day-price-movements',
                'category': 'Trading Strategies',
                'tags': 'swing trading, positional trading, technical analysis, trend trading',
                'excerpt': 'Learn how to profit from short to medium-term price swings with our detailed guide on swing trading strategies, timeframes, and risk management.',
                'content': '''
<h2>Understanding Swing Trading</h2>
<p>Swing trading is a strategy that aims to capture gains in a stock within a few days to several weeks. Unlike day trading, swing traders hold positions overnight and seek to profit from price "swings" in trending markets.</p>

<h2>Ideal Timeframe for Swing Trading</h2>
<ul>
<li><strong>Holding Period:</strong> 2 days to 6 weeks (typically 5-15 trading days)</li>
<li><strong>Chart Timeframes:</strong> Daily, 4-hour, and 1-hour charts</li>
<li><strong>Analysis Period:</strong> Review weekly charts for trends, daily for entry/exit</li>
</ul>

<h2>Core Swing Trading Strategies</h2>

<h3>1. Trend Following</h3>
<p>Identify stocks in strong uptrends or downtrends and trade in the direction of the trend. Use moving averages (20-day, 50-day) to confirm trend direction.</p>

<h3>2. Support and Resistance Breakouts</h3>
<p>Enter positions when price breaks above resistance (for longs) or below support (for shorts) with increased volume.</p>

<h3>3. Fibonacci Retracements</h3>
<p>Buy at 38.2%, 50%, or 61.8% retracement levels in an uptrend. These often act as support zones for continuation.</p>

<h3>4. Chart Pattern Trading</h3>
<p>Trade classic patterns like:</p>
<ul>
<li>Bull/Bear Flags</li>
<li>Head and Shoulders (reversal)</li>
<li>Cup and Handle (continuation)</li>
<li>Double Top/Bottom</li>
<li>Triangle Breakouts</li>
</ul>

<h2>Technical Indicators for Swing Trading</h2>

<h3>Must-Use Indicators:</h3>
<ul>
<li><strong>Moving Averages:</strong> 20 EMA and 50 SMA for trend identification</li>
<li><strong>RSI (14):</strong> Identify overbought (>70) and oversold (<30) conditions</li>
<li><strong>MACD:</strong> Trend and momentum confirmation</li>
<li><strong>Volume:</strong> Confirm breakouts and reversals</li>
<li><strong>Bollinger Bands:</strong> Volatility and potential reversal zones</li>
</ul>

<h2>Stock Selection Criteria</h2>
<p>Choose stocks that meet these criteria:</p>
<ol>
<li><strong>Liquidity:</strong> Average daily volume >5 lakh shares</li>
<li><strong>Volatility:</strong> Stocks that move 3-8% weekly</li>
<li><strong>Clear Trend:</strong> Strong uptrend or downtrend visible on daily chart</li>
<li><strong>Fundamental Support:</strong> Good quarterly results, sector tailwinds</li>
<li><strong>Price Range:</strong> ₹100-₹2000 for better position sizing</li>
</ol>

<h2>Entry and Exit Rules</h2>

<h3>Entry Points:</h3>
<ul>
<li>Buy on pullback to key moving average in uptrend</li>
<li>Enter on breakout above resistance with volume</li>
<li>Wait for confirmation candle after pattern completion</li>
</ul>

<h3>Exit Strategies:</h3>
<ul>
<li><strong>Profit Target:</strong> 5-15% gains depending on volatility</li>
<li><strong>Stop Loss:</strong> 2-5% below entry (adjust based on ATR)</li>
<li><strong>Trailing Stop:</strong> Move stop to breakeven after 50% of target reached</li>
<li><strong>Time Stop:</strong> Exit if no movement within expected timeframe</li>
</ul>

<h2>Risk Management</h2>
<ol>
<li><strong>Position Size:</strong> Risk only 1-2% of capital per trade</li>
<li><strong>Portfolio Diversification:</strong> Maximum 5-8 swing positions at once</li>
<li><strong>Sector Diversification:</strong> Spread across different sectors</li>
<li><strong>Risk-Reward Ratio:</strong> Minimum 1:2 (risk ₹100 to make ₹200)</li>
</ol>

<h2>Tax Benefits Over Day Trading</h2>
<p>Positions held for more than 1 day are treated as short-term capital gains (STCG) taxed at 15%, which is more favorable than speculative income taxation for intraday trades.</p>

<h2>Best Sectors for Swing Trading</h2>
<ul>
<li><strong>Banking & Finance:</strong> Responsive to RBI policy, credit growth</li>
<li><strong>IT Services:</strong> Influenced by dollar movements, tech trends</li>
<li><strong>Auto:</strong> Monthly sales data creates swing opportunities</li>
<li><strong>Pharma:</strong> News-driven with significant price swings</li>
<li><strong>Metals & Mining:</strong> Commodity price correlation</li>
</ul>

<h2>Common Swing Trading Mistakes</h2>
<ul>
<li>Holding losing positions too long hoping for reversal</li>
<li>Exiting winning trades too early (fear of giving back profits)</li>
<li>Not adapting to market conditions (trending vs. ranging)</li>
<li>Ignoring broader market trend (Nifty direction)</li>
<li>Over-leveraging with margin trading</li>
</ul>

<h2>Tools and Resources</h2>
<ul>
<li>TradingView or ChartInk for technical analysis</li>
<li>Screeners for finding setups (Chartink, Screener.in)</li>
<li>Economic calendar for important events</li>
<li>Sector rotation analysis tools</li>
</ul>

<p class="alert alert-info"><strong>Pro Tip:</strong> Swing trading works best in trending markets. In sideways/choppy markets, reduce position sizes or stick to range trading strategies.</p>
                ''',
                'author_name': 'Capulse Research Team',
                'is_featured': True
            },
            {
                'title': 'Technical Indicators: Complete Guide for Indian Traders',
                'slug': 'technical-indicators-complete-guide-indian-traders',
                'category': 'Technical Analysis',
                'tags': 'technical indicators, RSI, MACD, moving averages, technical analysis',
                'excerpt': 'Master the essential technical indicators used by successful traders. Learn how to use RSI, MACD, Moving Averages, and more to improve your trading decisions.',
                'content': '''
<h2>What are Technical Indicators?</h2>
<p>Technical indicators are mathematical calculations based on price, volume, or open interest that help traders analyze market trends, momentum, volatility, and potential reversal points.</p>

<h2>Types of Technical Indicators</h2>
<ul>
<li><strong>Trend Indicators:</strong> Identify direction of market movement</li>
<li><strong>Momentum Indicators:</strong> Measure speed of price changes</li>
<li><strong>Volatility Indicators:</strong> Measure price fluctuation range</li>
<li><strong>Volume Indicators:</strong> Analyze trading activity</li>
</ul>

<h2>Essential Trend Indicators</h2>

<h3>1. Moving Averages (MA)</h3>
<p><strong>Purpose:</strong> Smooth price data to identify trend direction</p>

<h4>Simple Moving Average (SMA)</h4>
<ul>
<li><strong>20-day SMA:</strong> Short-term trend</li>
<li><strong>50-day SMA:</strong> Medium-term trend</li>
<li><strong>200-day SMA:</strong> Long-term trend</li>
</ul>

<h4>Exponential Moving Average (EMA)</h4>
<p>Gives more weight to recent prices, responds faster to changes.</p>
<ul>
<li><strong>9 EMA & 21 EMA:</strong> Popular for intraday trading</li>
<li><strong>12 EMA & 26 EMA:</strong> Used in MACD calculation</li>
</ul>

<p><strong>Trading Signals:</strong></p>
<ul>
<li>Buy when price crosses above MA (bullish crossover)</li>
<li>Sell when price crosses below MA (bearish crossover)</li>
<li>Golden Cross: 50 MA crosses above 200 MA (strong buy)</li>
<li>Death Cross: 50 MA crosses below 200 MA (strong sell)</li>
</ul>

<h3>2. Supertrend Indicator</h3>
<p>Popular in Indian markets for its simplicity and effectiveness.</p>
<ul>
<li><strong>Settings:</strong> Period 10, Multiplier 3 (default)</li>
<li><strong>Buy Signal:</strong> Indicator turns green</li>
<li><strong>Sell Signal:</strong> Indicator turns red</li>
<li><strong>Best For:</strong> Trending markets, not choppy sideways movements</li>
</ul>

<h2>Key Momentum Indicators</h2>

<h3>1. Relative Strength Index (RSI)</h3>
<p><strong>Range:</strong> 0 to 100 | <strong>Default Period:</strong> 14</p>

<p><strong>Interpretation:</strong></p>
<ul>
<li><strong>Overbought:</strong> RSI > 70 (potential sell signal)</li>
<li><strong>Oversold:</strong> RSI < 30 (potential buy signal)</li>
<li><strong>Divergence:</strong> Price makes new high but RSI doesn't (bearish)</li>
<li><strong>Midline (50):</strong> Above 50 = bullish, below 50 = bearish</li>
</ul>

<p><strong>Trading Strategy:</strong></p>
<ol>
<li>In uptrend: Buy when RSI pulls back to 40-50</li>
<li>In downtrend: Sell when RSI bounces to 50-60</li>
<li>Extreme oversold (<20): Wait for reversal confirmation</li>
</ol>

<h3>2. MACD (Moving Average Convergence Divergence)</h3>
<p><strong>Components:</strong></p>
<ul>
<li>MACD Line: 12 EMA - 26 EMA</li>
<li>Signal Line: 9 EMA of MACD</li>
<li>Histogram: MACD - Signal Line</li>
</ul>

<p><strong>Trading Signals:</strong></p>
<ul>
<li><strong>Bullish Crossover:</strong> MACD crosses above signal line (buy)</li>
<li><strong>Bearish Crossover:</strong> MACD crosses below signal line (sell)</li>
<li><strong>Histogram:</strong> Increasing = strengthening trend</li>
<li><strong>Zero Line:</strong> Cross above = bullish, below = bearish</li>
</ul>

<h3>3. Stochastic Oscillator</h3>
<p><strong>Range:</strong> 0 to 100 | <strong>Settings:</strong> %K (14), %D (3)</p>
<ul>
<li><strong>Overbought:</strong> >80</li>
<li><strong>Oversold:</strong> <20</li>
<li><strong>Buy:</strong> %K crosses above %D in oversold zone</li>
<li><strong>Sell:</strong> %K crosses below %D in overbought zone</li>
</ul>

<h2>Volatility Indicators</h2>

<h3>1. Bollinger Bands</h3>
<p><strong>Components:</strong></p>
<ul>
<li>Middle Band: 20-day SMA</li>
<li>Upper Band: Middle + (2 × Standard Deviation)</li>
<li>Lower Band: Middle - (2 × Standard Deviation)</li>
</ul>

<p><strong>Trading Strategies:</strong></p>
<ul>
<li><strong>Bollinger Bounce:</strong> Buy at lower band, sell at upper band (ranging market)</li>
<li><strong>Bollinger Squeeze:</strong> Bands narrow = volatility breakout coming</li>
<li><strong>Breakout:</strong> Price close above upper band = strong bullish move</li>
</ul>

<h3>2. Average True Range (ATR)</h3>
<p><strong>Purpose:</strong> Measure market volatility, not direction</p>
<ul>
<li>High ATR = High volatility (wider stop loss needed)</li>
<li>Low ATR = Low volatility (consolidation phase)</li>
<li><strong>Stop Loss Placement:</strong> Entry ± (1.5 to 2 × ATR)</li>
</ul>

<h2>Volume Indicators</h2>

<h3>1. Volume (with Moving Average)</h3>
<ul>
<li><strong>Above Average:</strong> Confirms price movement strength</li>
<li><strong>Below Average:</strong> Weak or false breakout</li>
<li><strong>Volume Spike:</strong> Significant event or reversal</li>
</ul>

<h3>2. On-Balance Volume (OBV)</h3>
<p>Cumulative volume indicator showing money flow</p>
<ul>
<li>Rising OBV + Rising Price = Healthy uptrend</li>
<li>Falling OBV + Rising Price = Weak uptrend (divergence)</li>
</ul>

<h2>Combining Indicators Effectively</h2>

<h3>Beginner Combination:</h3>
<ul>
<li>20 EMA (trend)</li>
<li>RSI (momentum)</li>
<li>Volume (confirmation)</li>
</ul>

<h3>Intermediate Combination:</h3>
<ul>
<li>Moving Averages (50 & 200 SMA)</li>
<li>MACD (momentum & trend)</li>
<li>Bollinger Bands (volatility)</li>
<li>Volume</li>
</ul>

<h3>Advanced Combination:</h3>
<ul>
<li>Supertrend (trend direction)</li>
<li>RSI (entry timing)</li>
<li>ATR (stop loss placement)</li>
<li>Volume (confirmation)</li>
<li>Fibonacci (support/resistance)</li>
</ul>

<h2>Common Indicator Mistakes</h2>
<ol>
<li><strong>Too Many Indicators:</strong> Causes analysis paralysis (max 3-4)</li>
<li><strong>Using Same Type:</strong> Multiple momentum indicators give redundant signals</li>
<li><strong>Ignoring Context:</strong> Indicators work differently in trending vs. ranging markets</li>
<li><strong>Parameter Tweaking:</strong> Constantly changing settings to fit past data</li>
<li><strong>Indicator Alone:</strong> Always combine with price action and market context</li>
</ol>

<h2>Best Practices</h2>
<ul>
<li>Use trend indicator + momentum indicator + volume</li>
<li>Multiple timeframe analysis (daily + weekly for confirmation)</li>
<li>Paper trade new indicator combinations before live trading</li>
<li>Stick to one proven strategy consistently</li>
<li>Indicators lag price - use them for confirmation, not prediction</li>
</ul>

<h2>Recommended Setups by Trading Style</h2>

<p><strong>Day Trading:</strong> 9/21 EMA + RSI + Volume<br>
<strong>Swing Trading:</strong> 20/50 SMA + MACD + Bollinger Bands<br>
<strong>Position Trading:</strong> 50/200 SMA + Weekly RSI + Monthly MACD</p>

<p class="alert alert-success"><strong>Remember:</strong> No indicator is 100% accurate. Use them as tools to improve probability, not certainty. Always combine technical analysis with risk management.</p>
                ''',
                'author_name': 'Capulse Research Team',
                'is_featured': True
            },
            {
                'title': 'Options and Futures: Complete Guide for F&O Trading',
                'slug': 'options-futures-complete-guide-fo-trading',
                'category': 'Derivatives Trading',
                'tags': 'options trading, futures trading, F&O, derivatives, hedging strategies',
                'excerpt': 'Comprehensive guide to options and futures trading in India. Learn strategies, risk management, and how to trade derivatives profitably in NSE F&O segment.',
                'content': '''
<h2>Understanding Derivatives</h2>
<p>Derivatives are financial contracts whose value is derived from an underlying asset (stocks, indices, commodities). In India, NSE offers Futures and Options on stocks, Nifty, Bank Nifty, and other indices.</p>

<h2>Futures Trading</h2>

<h3>What are Futures?</h3>
<p>A futures contract is an agreement to buy/sell an asset at a predetermined price on a future date (expiry). Both buyer and seller are obligated to fulfill the contract.</p>

<h4>Key Features:</h4>
<ul>
<li><strong>Lot Size:</strong> Fixed quantity (e.g., Nifty = 50, Bank Nifty = 15)</li>
<li><strong>Expiry:</strong> Last Thursday of every month</li>
<li><strong>Margin:</strong> 15-40% of contract value (leverage 2.5-6x)</li>
<li><strong>Mark to Market:</strong> Daily profit/loss settlement</li>
<li><strong>No Premium:</strong> Pay only margin money</li>
</ul>

<h3>Futures Trading Strategies</h3>

<h4>1. Long Futures (Bullish)</h4>
<ul>
<li>Buy futures when expecting price rise</li>
<li>Profit = (Exit Price - Entry Price) × Lot Size</li>
<li>Best when: Strong uptrend, positive news flow</li>
</ul>

<h4>2. Short Futures (Bearish)</h4>
<ul>
<li>Sell futures when expecting price fall</li>
<li>Profit = (Entry Price - Exit Price) × Lot Size</li>
<li>Best when: Downtrend, negative fundamentals</li>
</ul>

<h4>3. Calendar Spread</h4>
<ul>
<li>Buy near month, sell far month (or vice versa)</li>
<li>Profit from price difference between months</li>
<li>Lower risk strategy for experienced traders</li>
</ul>

<h2>Options Trading</h2>

<h3>What are Options?</h3>
<p>Options give the RIGHT (not obligation) to buy/sell an asset at a specific price (strike price) before expiry.</p>

<h3>Types of Options</h3>

<h4>Call Option (CE)</h4>
<ul>
<li><strong>Buyer:</strong> Right to buy, pays premium, limited loss (premium), unlimited profit</li>
<li><strong>Seller:</strong> Obligation to sell, receives premium, limited profit (premium), unlimited loss</li>
<li><strong>Buy when:</strong> Bullish on market</li>
</ul>

<h4>Put Option (PE)</h4>
<ul>
<li><strong>Buyer:</strong> Right to sell, pays premium, limited loss (premium), high profit potential</li>
<li><strong>Seller:</strong> Obligation to buy, receives premium, limited profit (premium), high loss potential</li>
<li><strong>Buy when:</strong> Bearish on market</li>
</ul>

<h3>Important Option Terms</h3>
<ul>
<li><strong>Strike Price:</strong> Price at which option can be exercised</li>
<li><strong>Premium:</strong> Cost of buying option</li>
<li><strong>In The Money (ITM):</strong> Strike < Spot (Call), Strike > Spot (Put)</li>
<li><strong>At The Money (ATM):</strong> Strike ≈ Spot price</li>
<li><strong>Out of The Money (OTM):</strong> Strike > Spot (Call), Strike < Spot (Put)</li>
<li><strong>Intrinsic Value:</strong> Spot - Strike (Call) or Strike - Spot (Put)</li>
<li><strong>Time Value:</strong> Premium - Intrinsic Value</li>
</ul>

<h3>The Greeks</h3>

<h4>Delta (Δ)</h4>
<ul>
<li>Measures option price change per ₹1 change in underlying</li>
<li>Call: 0 to 1, Put: 0 to -1</li>
<li>ATM options have delta ~0.5</li>
</ul>

<h4>Theta (Θ)</h4>
<ul>
<li>Time decay - how much premium erodes daily</li>
<li>Accelerates in last week before expiry</li>
<li>Option sellers benefit from theta decay</li>
</ul>

<h4>Vega (V)</h4>
<ul>
<li>Sensitivity to volatility changes</li>
<li>Higher volatility = higher premiums</li>
<li>ATM options have highest vega</li>
</ul>

<h4>Gamma (Γ)</h4>
<ul>
<li>Rate of change of delta</li>
<li>Highest for ATM options near expiry</li>
<li>Important for dynamic hedging</li>
</ul>

<h2>Popular Options Strategies</h2>

<h3>1. Long Call (Bullish)</h3>
<ul>
<li><strong>When:</strong> Expect significant upside</li>
<li><strong>Risk:</strong> Limited to premium paid</li>
<li><strong>Profit:</strong> Unlimited (theoretically)</li>
<li><strong>Example:</strong> Buy Nifty 22000 CE when Nifty at 21800</li>
</ul>

<h3>2. Long Put (Bearish)</h3>
<ul>
<li><strong>When:</strong> Expect significant downside</li>
<li><strong>Risk:</strong> Limited to premium paid</li>
<li><strong>Profit:</strong> Substantial if sharp fall</li>
<li><strong>Example:</strong> Buy Nifty 21500 PE when Nifty at 21800</li>
</ul>

<h3>3. Bull Call Spread</h3>
<ul>
<li><strong>Strategy:</strong> Buy ATM Call + Sell OTM Call</li>
<li><strong>Risk:</strong> Limited (net premium paid)</li>
<li><strong>Profit:</strong> Limited (difference in strikes - net premium)</li>
<li><strong>Best for:</strong> Moderate bullish view, reduce cost</li>
</ul>

<h3>4. Bear Put Spread</h3>
<ul>
<li><strong>Strategy:</strong> Buy ATM Put + Sell OTM Put</li>
<li><strong>Risk:</strong> Limited (net premium paid)</li>
<li><strong>Profit:</strong> Limited (difference in strikes - net premium)</li>
<li><strong>Best for:</strong> Moderate bearish view</li>
</ul>

<h3>5. Straddle (High Volatility Play)</h3>
<ul>
<li><strong>Strategy:</strong> Buy ATM Call + Buy ATM Put (same strike, same expiry)</li>
<li><strong>When:</strong> Expect big move but unsure of direction</li>
<li><strong>Risk:</strong> Total premium paid (both options)</li>
<li><strong>Profit:</strong> Unlimited on either side</li>
<li><strong>Best for:</strong> Events like budget, RBI policy, election results</li>
</ul>

<h3>6. Strangle (Similar to Straddle, Lower Cost)</h3>
<ul>
<li><strong>Strategy:</strong> Buy OTM Call + Buy OTM Put</li>
<li><strong>When:</strong> Expect big move, lower premium than straddle</li>
<li><strong>Break-even:</strong> Needs bigger move than straddle</li>
</ul>

<h3>7. Iron Condor (Range-Bound Strategy)</h3>
<ul>
<li><strong>Strategy:</strong> Sell OTM Call + Buy further OTM Call + Sell OTM Put + Buy further OTM Put</li>
<li><strong>When:</strong> Expect range-bound market</li>
<li><strong>Risk:</strong> Limited (width of spread - net credit)</li>
<li><strong>Profit:</strong> Limited (net credit received)</li>
<li><strong>Best for:</strong> Low volatility, sideways market</li>
</ul>

<h2>F&O Risk Management</h2>

<h3>Position Sizing</h3>
<ul>
<li>Never use more than 25% of capital for F&O</li>
<li>Max risk per trade: 2-3% of F&O capital</li>
<li>Maintain adequate margin buffer (1.5-2x requirement)</li>
</ul>

<h3>Stop Loss Rules</h3>
<ul>
<li><strong>Futures:</strong> 1-2% of position value</li>
<li><strong>Option Buying:</strong> 30-50% of premium paid</li>
<li><strong>Option Selling:</strong> 2x premium received or when position doubles</li>
</ul>

<h3>Avoid These Mistakes</h3>
<ol>
<li>Buying deep OTM options hoping for lottery gains</li>
<li>Selling naked options without hedge (unlimited risk)</li>
<li>Holding positions till expiry (theta decay maximum)</li>
<li>Trading without understanding Greeks</li>
<li>Over-leveraging with full margin</li>
<li>Ignoring transaction costs (high in F&O)</li>
</ol>

<h2>Tax Implications</h2>
<ul>
<li><strong>F&O Gains:</strong> Treated as business income</li>
<li><strong>Tax Rate:</strong> As per your income slab (up to 30% + cess)</li>
<li><strong>Set-off:</strong> F&O losses can offset business income</li>
<li><strong>Audit:</strong> Required if turnover >₹10 crore or profit >10% of turnover</li>
</ul>

<h2>Best Practices</h2>
<ul>
<li>Start with index options (Nifty/Bank Nifty) - higher liquidity</li>
<li>Avoid stock options initially - lower liquidity, wider spreads</li>
<li>Trade during first hour (9:15-10:15 AM) for best liquidity</li>
<li>Close positions by 3:15 PM on expiry day</li>
<li>Use options simulator/paper trading before live trading</li>
<li>Understand options payoff graphs thoroughly</li>
<li>Track open interest and put-call ratio for market sentiment</li>
</ul>

<h2>Beginner's Roadmap</h2>
<ol>
<li><strong>Month 1-2:</strong> Learn theory, watch markets, paper trade</li>
<li><strong>Month 3-4:</strong> Start with option buying (long call/put) - limited risk</li>
<li><strong>Month 5-6:</strong> Try spreads (bull call, bear put)</li>
<li><strong>Month 7+:</strong> Advanced strategies (straddles, iron condors) only after consistent profits</li>
</ol>

<p class="alert alert-danger"><strong>Critical Warning:</strong> F&O trading is extremely risky. 95% of retail F&O traders lose money. Never trade with borrowed money or funds needed for essential expenses. Start small and scale gradually.</p>
                ''',
                'author_name': 'Capulse Research Team',
                'is_featured': False
            },
            {
                'title': 'Trading Psychology: Mastering Your Mind for Market Success',
                'slug': 'trading-psychology-mastering-mind-market-success',
                'category': 'Trading Psychology',
                'tags': 'trading psychology, emotional discipline, trading mindset, fear and greed',
                'excerpt': 'The psychological edge separates winning traders from losing ones. Learn to master emotions, develop discipline, and build a winning trading mindset.',
                'content': '''
<h2>Why Trading Psychology Matters</h2>
<p>Technical skills and strategies are important, but trading psychology is what determines long-term success. Research shows that 80% of trading success comes from psychology and only 20% from strategy. Most traders fail not because of bad strategies, but because they can't control their emotions.</p>

<h2>Core Emotions in Trading</h2>

<h3>1. Fear</h3>
<p><strong>Manifestations:</strong></p>
<ul>
<li>Fear of missing out (FOMO) - entering late into rallies</li>
<li>Fear of losing - exiting winning trades too early</li>
<li>Fear of being wrong - not admitting mistakes, holding losers</li>
<li>Fear of trading - paralysis after significant loss</li>
</ul>

<p><strong>How to Overcome:</strong></p>
<ul>
<li>Accept that losses are part of trading</li>
<li>Risk only what you can afford to lose (1-2% per trade)</li>
<li>Follow your trading plan mechanically</li>
<li>Use position sizing to control emotional impact</li>
</ul>

<h3>2. Greed</h3>
<p><strong>Manifestations:</strong></p>
<ul>
<li>Over-leveraging to maximize profits</li>
<li>Not booking profits, waiting for "more"</li>
<li>Revenge trading to recover losses quickly</li>
<li>Chasing every trading opportunity</li>
</ul>

<p><strong>How to Overcome:</strong></p>
<ul>
<li>Set realistic profit targets and stick to them</li>
<li>Maintain a trading journal to review greedy decisions</li>
<li>Remember: Missed opportunities are better than forced losses</li>
<li>Practice gratitude for current profits</li>
</ul>

<h3>3. Hope</h3>
<p><strong>The Killer Emotion:</strong></p>
<ul>
<li>Hoping losing position will recover</li>
<li>Averaging down in losing trades</li>
<li>Moving stop losses further away</li>
<li>Ignoring exit signals</li>
</ul>

<p><strong>Solution:</strong></p>
<ul>
<li>Use hard stop losses that execute automatically</li>
<li>Accept small losses to prevent large ones</li>
<li>"Hope is not a strategy" - exit when plan says to exit</li>
</ul>

<h2>Common Psychological Traps</h2>

<h3>1. Overconfidence Bias</h3>
<p>After a series of wins, traders believe they "figured out" the market and take excessive risks.</p>
<p><strong>Prevention:</strong> Maintain same risk per trade regardless of winning streak. Markets are random in short-term.</p>

<h3>2. Confirmation Bias</h3>
<p>Seeking information that confirms your position, ignoring contradicting signals.</p>
<p><strong>Prevention:</strong> Actively seek counter-arguments to your trade thesis. Play devil's advocate.</p>

<h3>3. Anchoring Bias</h3>
<p>Fixating on entry price or previous high/low, making irrational decisions.</p>
<p><strong>Prevention:</strong> Focus on what market is doing NOW, not what you paid or what "should" happen.</p>

<h3>4. Loss Aversion</h3>
<p>The pain of losing ₹10,000 is psychologically more intense than joy of gaining ₹10,000.</p>
<p><strong>Prevention:</strong> Focus on probabilities and expectancy, not individual trade outcomes.</p>

<h3>5. Recency Bias</h3>
<p>Giving more weight to recent trades/events.</p>
<p><strong>Prevention:</strong> Judge performance over 100+ trades, not last 5 trades.</p>

<h2>Building Mental Discipline</h2>

<h3>1. Develop a Trading Plan</h3>
<p>A comprehensive plan includes:</p>
<ul>
<li>Entry criteria (technical/fundamental)</li>
<li>Position sizing rules</li>
<li>Stop loss and profit target rules</li>
<li>Maximum trades per day/week</li>
<li>Daily/weekly loss limits</li>
<li>Market conditions to avoid trading</li>
</ul>

<h3>2. Follow the Plan Religiously</h3>
<ul>
<li>No trades outside your plan</li>
<li>Document all rule violations</li>
<li>Review and refine plan monthly</li>
<li>Backtest changes before implementing</li>
</ul>

<h3>3. Maintain a Trading Journal</h3>
<p><strong>Record for every trade:</strong></p>
<ul>
<li>Date, time, stock/index, direction</li>
<li>Entry/exit price and reason</li>
<li>Position size and risk amount</li>
<li>Emotions before, during, after trade</li>
<li>What you learned</li>
<li>Screenshot of chart setup</li>
</ul>

<p><strong>Weekly Review:</strong> Identify patterns in winning vs. losing trades. Are emotional trades profitable?</p>

<h3>4. Practice Mindfulness</h3>
<ul>
<li><strong>Pre-market routine:</strong> 10-minute meditation to center yourself</li>
<li><strong>During trading:</strong> Notice emotions without acting on them</li>
<li><strong>Breath awareness:</strong> Take 5 deep breaths before executing trades</li>
<li><strong>Post-market reflection:</strong> Journal emotional states and triggers</li>
</ul>

<h2>Dealing with Losses</h2>

<h3>Accepting Losses as Part of Business</h3>
<ul>
<li>Even best traders have 40-50% losing trades</li>
<li>Focus on overall expectancy, not win rate</li>
<li>Small losses are the cost of doing business</li>
</ul>

<h3>After a Loss:</h3>
<ol>
<li>Review if you followed your plan (process over outcome)</li>
<li>If plan was followed, accept it and move on</li>
<li>If plan was violated, understand why and how to prevent it</li>
<li>Take a break if emotionally affected (15-30 minutes)</li>
<li>Return with clear head, not to "revenge trade"</li>
</ol>

<h3>Losing Streak Protocol</h3>
<p>If you have 3 consecutive losses OR 5% daily loss:</p>
<ul>
<li>STOP trading immediately</li>
<li>Review all trades objectively</li>
<li>Check if market conditions changed</li>
<li>Reduce position size by 50% when resuming</li>
<li>Return to full size only after 5 consecutive profitable days</li>
</ul>

<h2>Managing Winning Streaks</h2>

<h3>When Everything Goes Right:</h3>
<ul>
<li><strong>Don't increase position size:</strong> Winning streaks end, often abruptly</li>
<li><strong>Don't get complacent:</strong> Market rewards discipline, punishes overconfidence</li>
<li><strong>Stick to process:</strong> If process is working, don't change it</li>
<li><strong>Book profits regularly:</strong> Transfer excess profits to separate account</li>
</ul>

<h2>Building the Right Mindset</h2>

<h3>Think in Probabilities</h3>
<ul>
<li>"This trade has 65% probability of success" not "This will definitely work"</li>
<li>Accept that even high-probability setups fail 35% of the time</li>
<li>Focus on taking many quality setups, not forcing each one to work</li>
</ul>

<h3>Process-Oriented Thinking</h3>
<ul>
<li>Good process + Bad outcome = Good trade</li>
<li>Bad process + Good outcome = Bad trade (don't repeat!)</li>
<li>Judge yourself on following rules, not on P&L</li>
</ul>

<h3>Abundance Mindset</h3>
<ul>
<li>Markets will be here tomorrow, next week, next year</li>
<li>Missing one opportunity is irrelevant</li>
<li>There will always be another high-probability setup</li>
<li>Patience is a competitive advantage</li>
</ul>

<h2>Practical Exercises</h2>

<h3>1. Visualization Exercise (Pre-market)</h3>
<ol>
<li>Close your eyes, take 5 deep breaths</li>
<li>Visualize following your plan perfectly</li>
<li>Imagine taking a loss and accepting it calmly</li>
<li>See yourself booking profits at target without greed</li>
<li>Feel the satisfaction of disciplined trading</li>
</ol>

<h3>2. Emotion Labeling</h3>
<p>When you feel strong emotion during trading:</p>
<ol>
<li>Pause and name the emotion (fear, greed, excitement)</li>
<li>Notice where you feel it in body</li>
<li>Take 3 deep breaths</li>
<li>Ask: "Is this emotion helping my decision?"</li>
<li>Choose action based on plan, not emotion</li>
</ol>

<h3>3. Weekly Psychology Review</h3>
<p>Every Sunday, answer:</p>
<ul>
<li>Which trades were emotional? What triggered them?</li>
<li>Which trades followed the plan? How did they perform?</li>
<li>What patterns do I notice in my behavior?</li>
<li>One psychological goal for next week?</li>
</ul>

<h2>Advanced Psychological Concepts</h2>

<h3>1. Flow State Trading</h3>
<ul>
<li>Clear goals and immediate feedback</li>
<li>Challenge matches skill level (not too easy, not too hard)</li>
<li>Deep focus without emotional interference</li>
<li>Achieved through routine and deliberate practice</li>
</ul>

<h3>2. Identity-Based Trading</h3>
<ul>
<li>Don't say "I want to be disciplined"</li>
<li>Say "I am a disciplined trader"</li>
<li>Identity drives behavior more than goals</li>
<li>Act according to your trading identity</li>
</ul>

<h3>3. Stoic Philosophy for Traders</h3>
<ul>
<li>Control what you can (risk, process, discipline)</li>
<li>Accept what you cannot (market direction, news, outcomes)</li>
<li>Emotional resilience comes from this distinction</li>
<li>Focus on inputs (process), let outputs (profits) take care of themselves</li>
</ul>

<h2>Signs You Need a Break</h2>
<ul>
<li>Constantly checking positions outside trading hours</li>
<li>Difficulty sleeping due to open positions</li>
<li>Arguing with family about trading</li>
<li>Feeling need to recover losses urgently</li>
<li>Trading becoming addictive rather than business</li>
<li>Physical symptoms: headaches, stomach issues, anxiety</li>
</ul>

<p><strong>Action:</strong> Take 1-2 week complete break. Reset mentally. Return with smaller position sizes.</p>

<h2>Creating Your Psychological Edge</h2>

<h3>Daily Routine:</h3>
<ul>
<li><strong>Morning (before market):</strong> Meditation, review plan, set intention</li>
<li><strong>During market:</strong> Follow plan mechanically, emotion awareness</li>
<li><strong>Evening:</strong> Journal trades, review psychology, plan tomorrow</li>
</ul>

<h3>Long-term Development:</h3>
<ul>
<li>Read books on trading psychology (Recommended: "Trading in the Zone" by Mark Douglas)</li>
<li>Work with trading psychologist if affordable</li>
<li>Join communities of disciplined traders</li>
<li>Continuous self-improvement</li>
</ul>

<h2>The Ultimate Truth</h2>
<p class="alert alert-info"><strong>Remember:</strong> Markets don't care about your opinions, hopes, or fears. They move based on supply and demand. Your only job is to align your actions with your plan, manage risk, and let probabilities work in your favor over time. Master yourself, and you master the markets.</p>

<p><strong>Key Takeaway:</strong> The trader who can control their emotions, stick to their plan through wins and losses, and continuously work on their psychology will outlast 95% of participants in this game. Success in trading is a marathon of discipline, not a sprint of profits.</p>
                ''',
                'author_name': 'Capulse Research Team',
                'is_featured': False
            }
        ]
        
        # Insert or update articles
        for article_data in articles:
            # Check if article already exists
            existing = BlogPost.query.filter_by(slug=article_data['slug']).first()
            
            if existing:
                # Update existing article
                for key, value in article_data.items():
                    setattr(existing, key, value)
                existing.updated_at = datetime.utcnow()
                existing.status = 'published'
                existing.published_at = datetime.utcnow()
                print(f"✅ Updated: {article_data['title']}")
            else:
                # Create new article
                article = BlogPost(
                    title=article_data['title'],
                    slug=article_data['slug'],
                    content=article_data['content'],
                    excerpt=article_data['excerpt'],
                    author_name=article_data['author_name'],
                    category=article_data['category'],
                    tags=article_data['tags'],
                    status='published',
                    published_at=datetime.utcnow(),
                    is_featured=article_data.get('is_featured', False)
                )
                db.session.add(article)
                print(f"✅ Created: {article_data['title']}")
        
        db.session.commit()
        print("\n🎉 All knowledge base articles created successfully!")
        print(f"📚 Total articles: {len(articles)}")

if __name__ == '__main__':
    create_articles()
