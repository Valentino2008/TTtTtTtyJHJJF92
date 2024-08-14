from dydx3 import Client
from dydx3.helpers.request_helpers import generate_now_iso
from decimal import Decimal, ROUND_DOWN, InvalidOperation, getcontext
import numpy as np
from datetime import datetime, timedelta
from web3 import Web3
import time
import pprint
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

# Función para calcular la media móvil
def calculate_ma(source, length, ma_type):
    if ma_type == "SMA":
        return np.convolve(source, np.ones(length), 'valid') / length
    elif ma_type == "EMA":
        ema = [sum(source[:length]) / length]
        multiplier = 2 / (length + 1)
        for price in source[length:]:
            ema.append((price - ema[-1]) * multiplier + ema[-1])
        return np.array(ema)
    elif ma_type == "SMMA":
        rma = [sum(source[:length]) / length]
        for price in source[length:]:
            rma.append((rma[-1] * (length - 1) + price) / length)
        return np.array(rma)
    elif ma_type == "WMA":
        weights = np.arange(1, length + 1)
        return np.convolve(source, weights / weights.sum(), 'valid')
    elif ma_type == "VWMA":
        vol_sum = np.cumsum(source[:, 1])
        return np.cumsum(source[:, 0] * source[:, 1]) / vol_sum

# Función para obtener el valor de la EMA y aplicarle un suavizado
def calculate_ema_smoothing(close_prices, ema_length=19, smoothing_length=20, smoothing_type="SMA"):
    ema = calculate_ma(close_prices, ema_length, "EMA")
    smoothing_line = calculate_ma(ema, smoothing_length, smoothing_type)
    return smoothing_line[-1]

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

def place_market_order(client, market, side, size, price, reduce_only):
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
        # Verificar el tamaño de la posición existente
        position_response = client.private.get_positions(market=market, status="OPEN")
        if position_response.data['positions']:
            position_size = Decimal(position_response.data['positions'][0]['size'])

            # Ajustar el tamaño de la orden para que no exceda la posición existente
            if reduce_only and size > abs(position_size):
                size = abs(position_size)
                print(f"Ajustando el tamaño de la orden a {size} para no exceder la posición existente en {market}.")

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

# Función para redondear el precio al múltiplo más cercano del tamaño del tick
def round_price(price, tick_size):
    try:
        price = Decimal(str(price)).normalize()
        tick_size = Decimal(str(tick_size)).normalize()
        return price.quantize(tick_size, rounding=ROUND_DOWN)
    except InvalidOperation as e:
        print(f"Error al redondear el precio: {e}")
        print(f"Precio: {price}, Tick Size: {tick_size}")
        return None

def execute_trades(client):
    markets = ["BTC-USD", "ETH-USD", "LINK-USD", "AAVE-USD", "DOGE-USD", "UNI-USD", "FIL-USD", "MATIC-USD", "SUSHI-USD", "AVAX-USD", "ADA-USD"]
    TOKEN_FACTOR_10 = ["XLM-USD", "DOGE-USD", "TRON-USD"]

    previous_decisions = {market: None for market in markets}

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
            smoothing_line = calculate_ema_smoothing(close_prices)

            min_order_size, tick_size, step_size = get_market_info(client, market)
            
            # Cálculo del tamaño de la orden basado en USD_PER_TRADE
            order_size = Decimal(USD_PER_TRADE) / current_price
            
            # Ajustes específicos para ciertos tokens
            if market in TOKEN_FACTOR_10:
                order_size = float(int(order_size / 10) * 10)

            if order_size < Decimal(str(min_order_size)):
                print(f"El tamaño de la orden para {market} es menor que el tamaño mínimo permitido.")
                continue

            formatted_order_size = format_number(order_size, step_size)
            if formatted_order_size is None:
                print(f"No se pudo formatear el tamaño de la orden para {market}.")
                continue

            # Verificar si hay una posición abierta
            position_response = client.private.get_positions(market=market, status="OPEN")
            position_open = bool(position_response.data['positions'])

            if position_open:
                position_size = Decimal(position_response.data['positions'][0]['size'])

            # Guardar el cruce actual (si está por encima o por debajo de la media móvil)
            current_decision = "above" if current_price > Decimal(str(smoothing_line)) else "below"

            # Esperar a que la vela se cierre antes de tomar acción
            if previous_decisions[market] != current_decision:
                print(f"Cambio detectado en {market}, esperando a que se cierre la vela actual para confirmar...")
                previous_decisions[market] = current_decision
                continue

            # Decidir si ir long, short, cerrar posiciones, o holdear basado en la media móvil
            if current_decision == "above":
                if position_open and position_size < 0:
                    # Cerrar posición corta y abrir posición larga si el precio está por encima de la media móvil
                    print(f"El precio actual de {market} está por encima de la línea suavizada. Cerrando posición corta y abriendo posición larga...")
                    place_market_order(client, market, "BUY", abs(position_size), current_price, True)
                    rounded_price = round_price(current_price, tick_size)
                    if rounded_price is None:
                        print(f"No se pudo redondear el precio para {market}.")
                        continue
                    place_market_order(client, market, "BUY", formatted_order_size, rounded_price, False)
                    print(f"Orden de compra colocada para {market} con tamaño {formatted_order_size} a precio {rounded_price}.")
                
                elif not position_open:
                    # Abrir posición larga si no hay posición abierta
                    print(f"El precio actual de {market} está por encima de la línea suavizada. Decidiendo comprar...")
                    rounded_price = round_price(current_price, tick_size)
                    if rounded_price is None:
                        print(f"No se pudo redondear el precio para {market}.")
                        continue
                    place_market_order(client, market, "BUY", formatted_order_size, rounded_price, False)
                    print(f"Orden de compra colocada para {market} con tamaño {formatted_order_size} a precio {rounded_price}.")

            elif current_decision == "below":
                if position_open and position_size > 0:
                    # Cerrar posición larga y abrir posición corta si el precio está por debajo de la media móvil
                    print(f"El precio actual de {market} está por debajo de la línea suavizada. Cerrando posición larga y abriendo posición corta...")
                    place_market_order(client, market, "SELL", abs(position_size), current_price, True)
                    rounded_price = round_price(current_price, tick_size)
                    if rounded_price is None:
                        print(f"No se pudo redondear el precio para {market}.")
                        continue
                    place_market_order(client, market, "SELL", formatted_order_size, rounded_price, False)
                    print(f"Orden de venta en corto colocada para {market} con tamaño {formatted_order_size} a precio {rounded_price}.")
                
                elif not position_open:
                    # Abrir posición corta si no hay posición abierta
                    print(f"El precio actual de {market} está por debajo de la línea suavizada. Decidiendo vender en corto...")
                    rounded_price = round_price(current_price, tick_size)
                    if rounded_price is None:
                        print(f"No se pudo redondear el precio para {market}.")
                        continue
                    place_market_order(client, market, "SELL", formatted_order_size, rounded_price, False)
                    print(f"Orden de venta en corto colocada para {market} con tamaño {formatted_order_size} a precio {rounded_price}.")

        # Esperar un intervalo de tiempo antes de la próxima revisión
        print("Esperando 10 segundos antes de la próxima revisión...")
        time.sleep(10)  # Puedes ajustar el tiempo de espera según tus necesidades


# Función principal
def main():
    client = connect_dydx()
    execute_trades(client)

if __name__ == "__main__":
    main()
