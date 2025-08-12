
from datetime import datetime
import pandas as pd

# Default parameters (kept in sync with app.DEFAULT_PARAMS for reuse)
DEFAULT_PARAMS = {
  "max_price_increase_above_A0": 0.50,
  "price_increase_to_confirm_A0": 0.05,
  "max_decrease_below_A0": 0.20,
  "price_increase_to_confirm_higher_low": 0.05,
  "pattern_time_limit_ms": 30*60*1000,
  "take_profit_offset": 0.50,
  "stop_loss_offset": 0.05,
  "time_to_wait_before_confirm_Ax_ms": 5*60*1000,
  "price_increase_from_A2_to_enter_trade": 0.02,
  "trade_timeout_ms": 45*60*1000
}

def detect_hl_patterns(df, p):
    events = []
    marks = []

    phase = 1
    a0 = None; a1 = None; a2 = None
    a0_confirmed_at = None
    a1_confirmed_at = None
    start_time = None
    in_trade = False
    entry_price = None
    entry_time = None

    wait_after_confirm = pd.Timedelta(milliseconds=p['time_to_wait_before_confirm_Ax_ms'])
    time_limit = pd.Timedelta(milliseconds=p['pattern_time_limit_ms'])

    def reset():
        nonlocal phase, a0, a1, a2, a0_confirmed_at, a1_confirmed_at, start_time, in_trade, entry_price, entry_time
        phase = 1
        a0 = a1 = a2 = None
        a0_confirmed_at = a1_confirmed_at = None
        start_time = None
        in_trade = False
        entry_price = None
        entry_time = None

    def disruption(candle_low):
        if a0 is None: 
            return False
        return (a0['low'] - candle_low) > p['max_decrease_below_A0']

    for i, row in df.iterrows():
        ts = row['ts']; low=row['low']; close=row['close']; high=row['high']
        if start_time is None:
            start_time = ts

        if (ts - start_time) > time_limit:
            reset()
            start_time = ts

        if phase == 1:
            if i>0 and low < df.iloc[i-1]['low']:
                if (a0 is None) or (low < a0['low']):
                    a0 = dict(time=ts, low=low)
                    marks.append({"ts": ts, "price": low, "label": "A0?"})
            if a0 and close >= a0['low'] + p['price_increase_to_confirm_A0']:
                a0_confirmed_at = ts
                phase = 2
                start_time = ts
                marks.append({"ts": ts, "price": close, "label": "A0✓"})
                continue

        elif phase == 2:
            if (ts - a0_confirmed_at) < wait_after_confirm:
                if low < a0['low']:
                    a0 = dict(time=ts, low=low)
                    marks.append({"ts": ts, "price": low, "label": "A0*"})
                continue
            if i>0 and low < df.iloc[i-1]['low']:
                if (low > a0['low']) and (low <= a0['low'] + p['max_price_increase_above_A0']):
                    if (a1 is None) or (low < a1['low']):
                        a1 = dict(time=ts, low=low)
                        marks.append({"ts": ts, "price": low, "label": "A1?"})
            if a1 and close >= a1['low'] + p['price_increase_to_confirm_higher_low']:
                a1_confirmed_at = ts
                phase = 3
                start_time = ts
                marks.append({"ts": ts, "price": close, "label": "A1✓"})
                continue
            if disruption(low):
                reset(); start_time = ts; continue

        elif phase == 3:
            if (ts - a1_confirmed_at) < wait_after_confirm:
                if a1 and low < a1['low']:
                    a1 = dict(time=ts, low=low)
                    marks.append({"ts": ts, "price": low, "label": "A1*"})
                continue
            if i>0 and low < df.iloc[i-1]['low']:
                if (low > a0['low']) and (low <= a0['low'] + p['max_price_increase_above_A0']):
                    if (a2 is None) or (low < a2['low']):
                        a2 = dict(time=ts, low=low)
                        marks.append({"ts": ts, "price": low, "label": "A2?"})
            if a2 and close >= a2['low'] + p['price_increase_to_confirm_higher_low']:
                phase = 4
                start_time = ts
                marks.append({"ts": ts, "price": close, "label": "A2✓"})
                continue
            if disruption(low):
                reset(); start_time = ts; continue

        elif phase == 4:
            if a2 and close <= (a2['low'] + p['price_increase_from_A2_to_enter_trade']):
                in_trade = True
                entry_price = close
                entry_time = ts
                marks.append({"ts": ts, "price": close, "label": "BUY"})
                phase = 5
                start_time = ts
                continue
            if disruption(low):
                reset(); start_time = ts; continue

        elif phase == 5:
            if in_trade:
                if row['high'] >= entry_price + p['take_profit_offset']:
                    events.append({
                        "a0_time": a0['time'], "a0_low": a0['low'],
                        "a1_time": a1['time'] if a1 else None, "a1_low": a1['low'] if a1 else None,
                        "a2_time": a2['time'] if a2 else None, "a2_low": a2['low'] if a2 else None,
                        "entry_time": entry_time, "entry_price": entry_price,
                        "exit_time": ts, "exit_price": entry_price + p['take_profit_offset'],
                        "outcome": "take_profit", "profit": p['take_profit_offset']
                    })
                    reset(); start_time = ts; continue
                if row['low'] <= entry_price - p['stop_loss_offset']:
                    events.append({
                        "a0_time": a0['time'], "a0_low": a0['low'],
                        "a1_time": a1['time'] if a1 else None, "a1_low": a1['low'] if a1 else None,
                        "a2_time": a2['time'] if a2 else None, "a2_low": a2['low'] if a2 else None,
                        "entry_time": entry_time, "entry_price": entry_price,
                        "exit_time": ts, "exit_price": entry_price - p['stop_loss_offset'],
                        "outcome": "stop_loss", "profit": -p['stop_loss_offset']
                    })
                    reset(); start_time = ts; continue
                if (ts - entry_time) >= pd.Timedelta(milliseconds=p['trade_timeout_ms']):
                    events.append({
                        "a0_time": a0['time'], "a0_low": a0['low'],
                        "a1_time": a1['time'] if a1 else None, "a1_low": a1['low'] if a1 else None,
                        "a2_time": a2['time'] if a2 else None, "a2_low": a2['low'] if a2 else None,
                        "entry_time": entry_time, "entry_price": entry_price,
                        "exit_time": ts, "exit_price": row['close'],
                        "outcome": "timeout", "profit": row['close'] - entry_price
                    })
                    reset(); start_time = ts; continue

    return events, marks

def run_backtest_on_df(df, params):
    events, _ = detect_hl_patterns(df, params)
    return events
