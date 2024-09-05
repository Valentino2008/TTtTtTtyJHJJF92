from dydx3 import Client
from decimal import Decimal, ROUND_DOWN, InvalidOperation, getcontext
import numpy as np
from datetime import datetime, timedelta
from web3 import Web3
import time

from constants import (
    HOST,
    ETHEREUM_ADDRESS,
    DYDX_API_KEY_TESTNET,
    DYDX_API_SECRET_TESTNET,
    DYDX_API_PASSPHRASE_TESTNET,
    STARK_PRIVATE_KEY_TESTNET,
    HTTP_PROVIDER_TESTNET,
    ETH_PRIVATE_KEY,
    USD_PER_TRADE
)

# Conectar a dYdX
def connect_dydx():
    client = Client(
        host=HOST,
        api_key_credentials={
            "key": DYDX_API_KEY_TESTNET,
            "secret": DYDX_API_SECRET_TESTNET,
            "passphrase": DYDX_API_PASSPHRASE_TESTNET,
        },
        stark_private_key=STARK_PRIVATE_KEY_TESTNET,
        eth_private_key=ETH_PRIVATE_KEY,
        default_ethereum_address=ETHEREUM_ADDRESS,
        web3=Web3(Web3.HTTPProvider(HTTP_PROVIDER_TESTNET))
    )
    return client

# Función para calcular la Media Móvil Simple (SMA)
def calculate_sma(prices, period):
    return np.convolve(prices, np.ones(period)/period, mode='valid')

# Función para calcular el CCI
def calculate_cci(high, low, close, period=9):
    tp = (high + low + close) / 3
    sma = np.convolve(tp, np.ones(period)/period, mode='valid')
    mean_deviation = np.mean(np.abs(tp[-period:] - sma[-1]))
    cci = (tp[-1] - sma[-1]) / (0.015 * mean_deviation)
    return cci

# Función para calcular el Golden Cross y Death Cross
def golden_death_cross(short_sma, long_sma):
    if short_sma[-1] > long_sma[-1]:
        return "golden_cross"
    elif short_sma[-1] < long_sma[-1]:
        return "death_cross"
    return None

# Función para obtener el tamaño mínimo de orden y el tick size para un mercado
def get_market_info(client, market):
    markets = client.public.get_markets().data['markets']
    min_order_size = float(markets[market]['minOrderSize'])
    tick_size = float(markets[market]['tickSize'])
    step_size = float(markets[market]['stepSize'])
    return min_order_size, tick_size, step_size

# Función para verificar si hay posiciones abiertas
def is_open_positions(client, market):
    time.sleep(0.2)
    all_positions = client.private.get_positions(market=market, status="OPEN")
    return len(all_positions.data["positions"]) > 0

def round_price(price, tick_size):
    try:
        tick_size = Decimal(str(tick_size)).normalize()
        price = Decimal(str(price)).quantize(tick_size, rounding=ROUND_DOWN)
        return price
    except InvalidOperation as e:
        print(f"Error al redondear el precio: {e}")
        print(f"Precio: {price}, Tick Size: {tick_size}")
        return None

# Función para colocar una orden de mercado
def place_market_order(client, market, side, size, price, reduce_only, stop_loss_price=None):
    min_order_size, tick_size, step_size = get_market_info(client, market)
    
    account_response = client.private.get_account()
    position_id = account_response.data["account"]["positionId"]
    server_time = client.public.get_time()
    expiration = datetime.fromisoformat(server_time.data["iso"].replace("Z", "")) + timedelta(seconds=70)

    # Redondear el tamaño de la orden y el precio
    size = format_number(size, step_size)
    price = round_price(price, tick_size)

    if size < min_order_size:
        print(f"El tamaño de la orden {size} es menor que el mínimo permitido {min_order_size}. No se enviará la orden.")
        return None

    try:
        placed_order = client.private.create_order(
            position_id=position_id,
            market=market,
            side=side,
            order_type="MARKET",
            post_only=False,
            size=str(size),
            price=str(price),
            limit_fee='0.015',
            expiration_epoch_seconds=expiration.timestamp(),
            time_in_force="FOK",
            reduce_only=reduce_only
        )
        print(f"Orden colocada para {market} con tamaño {size} a precio {price}.")
        return placed_order.data

    except Exception as e:
        print(f"Error al colocar la orden en {market}: {str(e)}")
        return None

getcontext().prec = 28

# Función para redondear el tamaño de la orden al múltiplo más cercano del tamaño del paso
def format_number(value, step_size):
    try:
        step_size = Decimal(str(step_size)).normalize()
        value = Decimal(str(value)).normalize()
        return value.quantize(step_size, rounding=ROUND_DOWN)
    except (InvalidOperation, ValueError) as e:
        print(f"Error al formatear el número: {e}")
        print(f"Valor: {value}, Step Size: {step_size}")
        return None

# Función principal para ejecutar las operaciones
def execute_trades(client):
    markets = ["BTC-USD", "ETH-USD", "LINK-USD", "AAVE-USD", "DOGE-USD", "UNI-USD", "FIL-USD", "MATIC-USD", "SUSHI-USD", "AVAX-USD", "ADA-USD"]

    while True:
        for market in markets:
            current_price = Decimal(str(client.public.get_markets().data['markets'][market]['indexPrice']))
            candles = client.public.get_candles(
                market=market,
                resolution="1DAY",
                from_iso=(datetime.utcnow() - timedelta(days=50)).isoformat(),
                to_iso=datetime.utcnow().isoformat()
            ).data['candles']
            
            close_prices = np.array([float(candle['close']) for candle in candles])
            high_prices = np.array([float(candle['high']) for candle in candles])
            low_prices = np.array([float(candle['low']) for candle in candles])
            
            short_sma = calculate_sma(close_prices, 9)
            long_sma = calculate_sma(close_prices, 21)
            cci = calculate_cci(high_prices, low_prices, close_prices)

            signal = golden_death_cross(short_sma, long_sma)
            
            if signal == "golden_cross" and cci > 100:
                if not is_open_positions(client, market):
                    place_market_order(client, market, "BUY", USD_PER_TRADE / current_price, current_price, reduce_only=False)

            elif signal == "death_cross" and cci < -100:
                if not is_open_positions(client, market):
                    place_market_order(client, market, "SELL", USD_PER_TRADE / current_price, current_price, reduce_only=False)
            
            time.sleep(1)
        time.sleep(60)
        print("[̲̅$̲̅( ͡❛ 👅 ͡❛)̲̅$̲̅]")

client = connect_dydx()
execute_trades(client)
