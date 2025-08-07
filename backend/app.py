from flask import Flask, request, jsonify, make_response
from flask_cors import CORS
import pandas as pd
import io
import requests
import time
from fpdf import FPDF
from datetime import datetime
import locale

app = Flask(__name__)
CORS(app)

COINGECKO_API_URL = "https://api.coingecko.com/api/v3/simple/price"
EXCHANGERATE_API_URL = "https://api.exchangerate.host/latest"
ASSET_ID_MAP = {
    'BTC': 'bitcoin',
    'ETH': 'ethereum',
    'SOL': 'solana',
    'ADA': 'cardano',
    'DOGE': 'dogecoin',
}
CACHE = {}
CACHE_EXPIRATION = 60  # 1 minute

def get_currency_rates(base_currency='USD'):
    # Hardcoded for now
    return {"USD": 1.0, "INR": 83.0, "GBP": 0.8}

def get_current_prices(asset_symbols, vs_currency='usd'):
    now = time.time()
    prices = {}

    cache_key = f"prices_{vs_currency}"
    if cache_key not in CACHE:
        CACHE[cache_key] = {}

    cached_symbols = {s for s in asset_symbols if s in CACHE[cache_key] and now - CACHE[cache_key][s]['timestamp'] < CACHE_EXPIRATION}
    for s in cached_symbols:
        prices[s] = CACHE[cache_key][s]['price']

    uncached_symbols = [s for s in asset_symbols if s not in cached_symbols]
    if uncached_symbols:
        ids = [ASSET_ID_MAP.get(s.upper()) for s in uncached_symbols if ASSET_ID_MAP.get(s.upper())]
        if ids:
            try:
                response = requests.get(COINGECKO_API_URL, params={'ids': ','.join(ids), 'vs_currencies': vs_currency})
                response.raise_for_status()
                data = response.json()
                for symbol in uncached_symbols:
                    coin_id = ASSET_ID_MAP.get(symbol.upper())
                    if coin_id in data:
                        price = data[coin_id][vs_currency]
                        prices[symbol] = price
                        CACHE[cache_key][symbol] = {'timestamp': now, 'price': price}
            except requests.exceptions.RequestException as e:
                print(f"Error fetching from CoinGecko: {e}")
                for symbol in uncached_symbols:
                    prices[symbol] = CACHE[cache_key].get(symbol, {}).get('price', 0)
    return prices

def generate_tax_saving_tips(unrealized_gains, country):
    tips = []

    # Specific tips based on unrealized losses
    unrealized_losses = [g for g in unrealized_gains if g['Gain'] < 0]
    if unrealized_losses:
        total_loss = sum(g['Gain'] for g in unrealized_losses)
        if country == 'USA':
            tips.append(f"Tax-Loss Harvesting Opportunity: You have unrealized losses of {total_loss:,.2f}. You can sell these assets to realize the loss and offset other capital gains, potentially lowering your tax bill. Remember the wash sale rule if you plan to repurchase the same asset within 30 days.")
        elif country == 'UK':
            tips.append(f"Tax-Loss Harvesting Opportunity: You have unrealized losses of {total_loss:,.2f}. You can sell these assets to realize the loss and offset other capital gains. Be mindful of the 'bed and breakfasting' rule if you buy back the same asset within 30 days.")
        elif country == 'India':
            tips.append(f"Loss Offset Opportunity: You have unrealized losses of {total_loss:,.2f}. In India, you can offset losses from one crypto asset against gains from another within the same financial year. Selling these assets could help reduce your total taxable crypto income.")

    # Generic tips if no specific tips were generated
    if not tips:
        if country == 'USA':
            tips.extend([
                "General Tip: Consider holding assets for over a year to potentially qualify for lower long-term capital gains tax rates.",
                "General Tip: You can donate cryptocurrency to a qualified charity to potentially receive a tax deduction against your income."
            ])
        elif country == 'UK':
            tips.extend([
                "General Tip: The UK provides a Capital Gains Tax allowance each year. Gains under this amount are not taxed, so consider realizing gains up to this allowance annually.",
                "General Tip: Keep meticulous records of all your transactions, as they are essential for accurate tax reporting."
            ])
        elif country == 'India':
            tips.extend([
                "General Tip: Crypto gains in India are taxed at a flat 30% under Section 115BBH, with no distinction between long-term and short-term gains.",
                "General Tip: Be aware that losses from crypto assets cannot be offset against any other income."
            ])
    return tips

def calculate_taxable_gain(realized_gains, country):
    total_gain = sum(g['Gain'] for g in realized_gains)

    if country == 'India':
        return max(0, total_gain) * 0.30
    elif country == 'USA':
        short_term_gains = sum(g['Gain'] for g in realized_gains if g['Type'] == 'short-term')
        long_term_gains = sum(g['Gain'] for g in realized_gains if g['Type'] == 'long-term')
        return short_term_gains + long_term_gains
    elif country == 'UK':
        allowance = 12300
        taxable_gain = total_gain - allowance
        return max(0, taxable_gain)
    else:
        return total_gain

def calculate_gains(transactions, country='USA'):
    currency_map = {
        'USA': ('USD', '$'),
        'India': ('INR', '₹'),
        'UK': ('GBP', '£')
    }
    vs_currency, currency_symbol = currency_map.get(country, ('USD', '$'))
    rates = get_currency_rates()
    rate = rates.get(vs_currency.upper(), 1)

    realized_gains = []
    unrealized_gains = []

    # Ensure numeric columns are numeric
    for col in ['Amount', 'Price', 'Fees']:
        if col in transactions.columns:
            transactions[col] = pd.to_numeric(transactions[col], errors='coerce').fillna(0)

    transactions['Date'] = pd.to_datetime(transactions['Date'])
    transactions = transactions.to_dict(orient='records')
    for i, t in enumerate(transactions):
        t['tx_id'] = i
    transactions_by_asset = {}
    for t in transactions:
        asset = t['Asset']
        if asset not in transactions_by_asset:
            transactions_by_asset[asset] = []
        transactions_by_asset[asset].append(t)

    asset_symbols = list(transactions_by_asset.keys())
    current_prices = get_current_prices(asset_symbols, vs_currency.lower())

    for asset, asset_transactions in transactions_by_asset.items():
        asset_transactions.sort(key=lambda x: x['Date'])

        buys = {t['tx_id']: t for t in asset_transactions if t['Type'].lower() == 'buy'}

        for t in asset_transactions:
            if t['Type'].lower() == 'sell':
                sell_amount = float(t['Amount'])
                sell_price = float(t['Price']) * rate
                proceeds = sell_amount * sell_price
                cost_basis = 0

                buy_id = t.get('buy_id')
                if buy_id in buys:
                    buy = buys[buy_id]
                    buy_price = float(buy['Price']) * rate
                    fees = float(t.get('Fees', 0)) * rate
                    cost_basis = (sell_amount * buy_price) + fees
                    buy['Amount'] -= sell_amount

                    holding_period = (t['Date'] - buy['Date']).days
                    gain_type = 'long-term' if holding_period > 365 else 'short-term'

                    if buy['Amount'] <= 0:
                        del buys[buy_id]
                else: # Fallback to FIFO
                    temp_buys = sorted(list(buys.values()), key=lambda x: x['Date'])
                    temp_sell_amount = sell_amount

                    # Create a list of buys to remove from the original `buys` dict
                    buys_to_remove = []

                    for buy in temp_buys:
                        if temp_sell_amount <= 0:
                            break

                        buy_amount = float(buy['Amount'])
                        buy_price = float(buy['Price']) * rate
                        fees = float(t.get('Fees', 0)) * rate

                        holding_period = (t['Date'] - buy['Date']).days
                        gain_type = 'long-term' if holding_period > 365 else 'short-term'

                        amount_to_sell = min(temp_sell_amount, buy_amount)

                        cost_basis += (amount_to_sell * buy_price) + (fees * (amount_to_sell / sell_amount))
                        buy['Amount'] -= amount_to_sell
                        temp_sell_amount -= amount_to_sell

                        if buy['Amount'] <= 0.000001: # Use a small epsilon for float comparison
                            buys_to_remove.append(buy['tx_id'])

                    for buy_tx_id in buys_to_remove:
                        if buy_tx_id in buys:
                            del buys[buy_tx_id]

                gain = proceeds - cost_basis

                tax_owed = 0
                if country == 'India':
                    tax_owed = max(0, gain) * 0.30
                elif country == 'USA':
                    if gain_type == 'short-term':
                        tax_owed = gain * 0.37 # Simplified
                    else:
                        tax_owed = gain * 0.20 # Simplified
                elif country == 'UK':
                    tax_owed = max(0, gain) * 0.20 # Simplified

                realized_gains.append({
                    'Asset': asset,
                    'Date': t['Date'].strftime('%Y-%m-%d'),
                    'Gain': gain,
                    'Proceeds': proceeds,
                    'Cost_Basis': cost_basis,
                    'Type': gain_type,
                    'Transaction_Type': t['Type'],
                    'Tax_Owed': tax_owed,
                    'Amount': float(t['Amount']),
                    'Price': float(t['Price']) * rate,
                    'Fees': float(t.get('Fees', 0)) * rate
                })

        current_price = current_prices.get(asset, 0)
        remaining_buys = list(buys.values())

        for buy in remaining_buys:
            cost_basis = (float(buy['Amount']) * float(buy['Price']) * rate) + (float(buy.get('Fees', 0)) * rate)
            market_value = float(buy['Amount']) * current_price
            gain = market_value - cost_basis
            unrealized_gains.append({
                'Asset': asset,
                'Amount': float(buy['Amount']),
                'Cost_Basis': cost_basis,
                'Market_Value': market_value,
                'Gain': gain,
                'Current_Price': current_price,
                'Date': buy['Date'].strftime('%Y-%m-%d'),
                'Transaction_Type': 'buy'
            })

    taxable_gain = calculate_taxable_gain(realized_gains, country)
    tax_saving_tips = generate_tax_saving_tips(unrealized_gains, country)
    return realized_gains, unrealized_gains, taxable_gain, currency_symbol, tax_saving_tips

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400

    country = request.form.get('country', 'USA')

    if file:
        try:
            if file.filename.endswith('.csv'):
                df = pd.read_csv(io.StringIO(file.read().decode('utf-8')))
            elif file.filename.endswith('.json'):
                df = pd.read_json(io.StringIO(file.read().decode('utf-8')))
            else:
                return jsonify({'error': 'Unsupported file type'}), 400

            realized_gains, unrealized_gains, taxable_gain, currency_symbol, tax_saving_tips = calculate_gains(df, country=country)
            return jsonify({
                'realized_gains': realized_gains,
                'unrealized_gains': unrealized_gains,
                'taxable_gain': taxable_gain,
                'currency_symbol': currency_symbol,
                'tax_saving_tips': tax_saving_tips,
                'country': country
            })
        except Exception as e:
            return jsonify({'error': str(e)}), 500

@app.route('/report_pdf', methods=['POST'])
def generate_pdf_report():
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400

    realized_gains = data.get('realized_gains', [])
    unrealized_gains = data.get('unrealized_gains', [])
    taxable_gain = data.get('taxable_gain', 0)
    currency_symbol = data.get('currency_symbol', '$')
    tax_saving_tips = data.get('tax_saving_tips', [])
    country = data.get('country', 'USA')

    pdf = FPDF()
    pdf.add_page()

    # Define a text formatting function based on font availability
    def format_text_for_pdf(text):
        # Default behavior: handle rupee symbol
        s = str(text).replace("₹", "INR ")
        # If using fallback font, also encode for safety
        if font_family == "Helvetica":
            return s.encode('latin-1', 'replace').decode('latin-1')
        return s

    try:
        pdf.add_font("DejaVu", "", "DejaVuSans.ttf")
        pdf.add_font("DejaVu", "B", "DejaVuSans-Bold.ttf")
        font_family = "DejaVu"
    except FileNotFoundError:
        # Fallback to standard font if DejaVu is not available
        font_family = "Helvetica"

    pdf.set_font(font_family, 'B', 16)

    title = f"Tax Report ({country} - {currency_symbol})"
    pdf.cell(0, 10, text=format_text_for_pdf(title), new_x="LMARGIN", new_y="NEXT", align='C')

    pdf.set_font(font_family, size=12)
    taxable_gain_text = f"Taxable Gain: {taxable_gain:,.2f} {currency_symbol}"
    pdf.cell(0, 10, text=format_text_for_pdf(taxable_gain_text), new_x="LMARGIN", new_y="NEXT", align='L')

    if tax_saving_tips:
        pdf.ln(5)
        pdf.set_font(font_family, 'B', 12)
        pdf.cell(0, 10, text=format_text_for_pdf("Tax-Saving Tips"), new_x="LMARGIN", new_y="NEXT", align='L')
        pdf.set_font(font_family, size=12)
        for tip in tax_saving_tips:
            pdf.multi_cell(0, 5, text=format_text_for_pdf(tip))
        pdf.ln(5)

    if realized_gains:
        pdf.ln(5)
        pdf.set_font(font_family, 'B', 12)
        pdf.cell(0, 10, text=format_text_for_pdf("Realized Gains"), new_x="LMARGIN", new_y="NEXT", align='L')
        pdf.set_font(font_family, size=10)

        col_width = pdf.epw / 8
        row_height = pdf.font_size * 1.5

        headers = ["Asset", "Date", "Type", "Proceeds", "Cost Basis", "Gain", "Gain Type", "Tax Owed"]
        for header in headers:
            pdf.cell(col_width, row_height, format_text_for_pdf(header), border=1)
        pdf.ln(row_height)

        total_realized_gain = 0
        total_tax_owed = 0
        for gain in realized_gains:
            pdf.cell(col_width, row_height, format_text_for_pdf(gain['Asset']), border=1)
            pdf.cell(col_width, row_height, format_text_for_pdf(gain['Date']), border=1)
            pdf.cell(col_width, row_height, format_text_for_pdf(gain['Transaction_Type']), border=1)
            pdf.cell(col_width, row_height, format_text_for_pdf(f"{gain['Proceeds']:,.2f} {currency_symbol}"), border=1)
            pdf.cell(col_width, row_height, format_text_for_pdf(f"{gain['Cost_Basis']:,.2f} {currency_symbol}"), border=1)
            pdf.cell(col_width, row_height, format_text_for_pdf(f"{gain['Gain']:,.2f} {currency_symbol}"), border=1)
            pdf.cell(col_width, row_height, format_text_for_pdf(gain['Type']), border=1)
            pdf.cell(col_width, row_height, format_text_for_pdf(f"{gain['Tax_Owed']:,.2f} {currency_symbol}"), border=1)
            pdf.ln(row_height)
            total_realized_gain += gain['Gain']
            total_tax_owed += gain['Tax_Owed']
        pdf.set_font(font_family, 'B', 12)
        total_realized_text = f"Total Realized Gain: {total_realized_gain:,.2f} {currency_symbol}"
        total_tax_text = f"Total Tax Owed: {total_tax_owed:,.2f} {currency_symbol}"
        pdf.cell(0, 10, text=format_text_for_pdf(total_realized_text), new_x="LMARGIN", new_y="NEXT", align='L')
        pdf.cell(0, 10, text=format_text_for_pdf(total_tax_text), new_x="LMARGIN", new_y="NEXT", align='L')

    if unrealized_gains:
        pdf.ln(10)
        pdf.set_font(font_family, 'B', 12)
        pdf.cell(0, 10, text=format_text_for_pdf("Unrealized Gains"), new_x="LMARGIN", new_y="NEXT", align='L')
        pdf.set_font(font_family, size=10)
        col_width = pdf.epw / 6
        row_height = pdf.font_size * 1.5

        headers = ["Asset", "Amount", "Cost Basis", "Market Value", "Gain", "Current Price"]
        for header in headers:
            pdf.cell(col_width, row_height, format_text_for_pdf(header), border=1)
        pdf.ln(row_height)

        total_unrealized_gain = 0
        for gain in unrealized_gains:
            pdf.cell(col_width, row_height, format_text_for_pdf(gain['Asset']), border=1)
            pdf.cell(col_width, row_height, format_text_for_pdf(gain['Amount']), border=1)
            pdf.cell(col_width, row_height, format_text_for_pdf(f"{gain['Cost_Basis']:,.2f} {currency_symbol}"), border=1)
            pdf.cell(col_width, row_height, format_text_for_pdf(f"{gain['Market_Value']:,.2f} {currency_symbol}"), border=1)
            pdf.cell(col_width, row_height, format_text_for_pdf(f"{gain['Gain']:,.2f} {currency_symbol}"), border=1)
            pdf.cell(col_width, row_height, format_text_for_pdf(f"{gain['Current_Price']:,.2f} {currency_symbol}"), border=1)
            pdf.ln(row_height)
            total_unrealized_gain += gain['Gain']
        pdf.set_font(font_family, 'B', 12)
        total_unrealized_text = f"Total Unrealized Gain: {total_unrealized_gain:,.2f} {currency_symbol}"
        pdf.cell(0, 10, text=format_text_for_pdf(total_unrealized_text), new_x="LMARGIN", new_y="NEXT", align='L')

    response = make_response(bytes(pdf.output()))
    response.headers.set('Content-Disposition', 'attachment', filename='tax_report.pdf')
    response.headers.set('Content-Type', 'application/pdf')
    return response

if __name__ == '__main__':
    app.run(debug=True, port=5001)