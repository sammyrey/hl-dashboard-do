
import os
from datetime import datetime, date, timedelta, timezone
import pytz
import pandas as pd
import numpy as np

from dash import Dash, dcc, html, Input, Output, State, dash_table
import dash_bootstrap_components as dbc
import plotly.graph_objs as go

from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

from hl_fsm import detect_hl_patterns, run_backtest_on_df
from polygon_client import fetch_aggs_range

LOG_LEVEL = os.getenv("LOG_LEVEL","INFO").upper()

POLYGON_API_KEY = os.environ.get("POLYGON_API_KEY")
SUPABASE_DB_URL = os.environ.get("SUPABASE_DB_URL")  # postgres connection string
if SUPABASE_DB_URL and "sslmode" not in SUPABASE_DB_URL:
    if "?" in SUPABASE_DB_URL:
        SUPABASE_DB_URL += "&sslmode=require"
    else:
        SUPABASE_DB_URL += "?sslmode=require"

engine = create_engine(SUPABASE_DB_URL, poolclass=NullPool) if SUPABASE_DB_URL else None

US_EASTERN = pytz.timezone("America/New_York")

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

def is_market_hours(now_utc=None):
    now_utc = now_utc or datetime.now(timezone.utc)
    now_et = now_utc.astimezone(US_EASTERN)
    start = now_et.replace(hour=9, minute=0, second=0, microsecond=0)
    end   = now_et.replace(hour=16, minute=30, second=0, microsecond=0)
    return start <= now_et <= end and now_et.weekday() < 5

def last_trading_day_et(today=None):
    d = today or datetime.now(US_EASTERN).date()
    while d.weekday() >= 5:
        d = d - timedelta(days=1)
    return d

external_stylesheets = [dbc.themes.BOOTSTRAP]
app = Dash(__name__, external_stylesheets=external_stylesheets, suppress_callback_exceptions=True)
server = app.server

def parameter_inputs(prefix=""):
    def num(id_, val, step='any'):
        return dbc.Input(id=id_, type="number", value=val, step=step, debounce=True, size='sm')
    return dbc.Container([
        dbc.Row([dbc.Col(html.Label("max_price_increase_above_A0 ($)")), dbc.Col(num(f"{prefix}max_price_increase_above_A0", DEFAULT_PARAMS["max_price_increase_above_A0"]))]),
        dbc.Row([dbc.Col(html.Label("price_increase_to_confirm_A0 ($)")), dbc.Col(num(f"{prefix}price_increase_to_confirm_A0", DEFAULT_PARAMS["price_increase_to_confirm_A0"]))]),
        dbc.Row([dbc.Col(html.Label("max_decrease_below_A0 ($)")), dbc.Col(num(f"{prefix}max_decrease_below_A0", DEFAULT_PARAMS["max_decrease_below_A0"]))]),
        dbc.Row([dbc.Col(html.Label("price_increase_to_confirm_higher_low ($)")), dbc.Col(num(f"{prefix}price_increase_to_confirm_higher_low", DEFAULT_PARAMS["price_increase_to_confirm_higher_low"]))]),
        dbc.Row([dbc.Col(html.Label("pattern_time_limit_ms")), dbc.Col(num(f"{prefix}pattern_time_limit_ms", DEFAULT_PARAMS["pattern_time_limit_ms"]))]),
        dbc.Row([dbc.Col(html.Label("take_profit_offset ($)")), dbc.Col(num(f"{prefix}take_profit_offset", DEFAULT_PARAMS["take_profit_offset"]))]),
        dbc.Row([dbc.Col(html.Label("stop_loss_offset ($)")), dbc.Col(num(f"{prefix}stop_loss_offset", DEFAULT_PARAMS["stop_loss_offset"]))]),
        dbc.Row([dbc.Col(html.Label("time_to_wait_before_confirm_Ax_ms")), dbc.Col(num(f"{prefix}time_to_wait_before_confirm_Ax_ms", DEFAULT_PARAMS["time_to_wait_before_confirm_Ax_ms"]))]),
        dbc.Row([dbc.Col(html.Label("price_increase_from_A2_to_enter_trade ($)")), dbc.Col(num(f"{prefix}price_increase_from_A2_to_enter_trade", DEFAULT_PARAMS["price_increase_from_A2_to_enter_trade"]))]),
        dbc.Row([dbc.Col(html.Label("trade_timeout_ms")), dbc.Col(num(f"{prefix}trade_timeout_ms", DEFAULT_PARAMS["trade_timeout_ms"]))]),
    ], fluid=True)

app.layout = dbc.Container([
    html.H3("Higher Low Pattern Dashboard"),
    dcc.Tabs(id="tabs", value="landing", children=[
        dcc.Tab(label="Landing", value="landing"),
        dcc.Tab(label="Pattern Backtesting", value="backtest"),
        dcc.Tab(label="Pattern Fine Tuning", value="fine"),
    ]),
    html.Div(id="tab-content")
], fluid=True)

landing_layout = dbc.Container([
    dbc.Row([
        dbc.Col(dbc.Input(id="symbol", placeholder="Symbol (e.g., AAPL)", value="AAPL"), md=3),
        dbc.Col(dbc.Button("Refresh Now", id="btn-refresh", color="primary"), md=2),
        dbc.Col(html.Div(id="market-status"), md=7)
    ], className="my-2"),
    dcc.Graph(id="main-chart")
], fluid=True)

backtest_layout = dbc.Container([
    dbc.Row([
        dbc.Col(dbc.Input(id="bt-symbol", placeholder="Symbol", value="AAPL"), md=2),
        dbc.Col(dcc.DatePickerRange(id="bt-range"), md=4),
        dbc.Col(dbc.Button("Run Backtest", id="btn-bt", color="primary"), md=2),
        dbc.Col(dbc.Button("Parameters", id="btn-bt-params", color="secondary"), md=2),
    ], className="my-2"),
    dbc.Collapse(parameter_inputs(prefix="bt-"), id="bt-params-collapse", is_open=False),
    html.Hr(),
    html.Div(id="bt-summary"),
    dash_table.DataTable(id="bt-by-period", page_size=10, sort_action="native")
], fluid=True)

def param_columns():
    return [
        {"name":"Set #", "id":"set_id", "editable": False},
        {"name":"max_price_increase_above_A0", "id":"max_price_increase_above_A0", "type":"numeric"},
        {"name":"price_increase_to_confirm_A0", "id":"price_increase_to_confirm_A0", "type":"numeric"},
        {"name":"max_decrease_below_A0", "id":"max_decrease_below_A0", "type":"numeric"},
        {"name":"price_increase_to_confirm_higher_low", "id":"price_increase_to_confirm_higher_low", "type":"numeric"},
        {"name":"pattern_time_limit_ms", "id":"pattern_time_limit_ms", "type":"numeric"},
        {"name":"take_profit_offset", "id":"take_profit_offset", "type":"numeric"},
        {"name":"stop_loss_offset", "id":"stop_loss_offset", "type":"numeric"},
        {"name":"time_to_wait_before_confirm_Ax_ms", "id":"time_to_wait_before_confirm_Ax_ms", "type":"numeric"},
        {"name":"price_increase_from_A2_to_enter_trade", "id":"price_increase_from_A2_to_enter_trade", "type":"numeric"},
        {"name":"trade_timeout_ms", "id":"trade_timeout_ms", "type":"numeric"},
    ]

def default_ft_rows(n=10):
    rows = []
    DEFAULT = {
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
    for i in range(1, n+1):
        r = dict(set_id=i, **DEFAULT)
        rows.append(r)
    return rows

fine_layout = dbc.Container([
    dbc.Row([
        dbc.Col(dbc.Input(id="ft-symbol", placeholder="Symbol", value="AAPL"), md=2),
        dbc.Col(dcc.DatePickerRange(id="ft-range"), md=4),
        dbc.Col(dbc.Button("Run Fine Tuning", id="btn-ft", color="primary"), md=2),
        dbc.Col(dbc.Button("Parameters Grid (10 sets)", id="btn-ft-params", color="secondary"), md=3),
    ], className="my-2"),
    dbc.Collapse(
        dash_table.DataTable(
            id="ft-table", columns=param_columns(), data=default_ft_rows(), editable=True, page_size=10
        ), id="ft-params-collapse", is_open=True
    ),
    html.Hr(),
    dash_table.DataTable(id="ft-results", page_size=10, sort_action="native")
], fluid=True)

@app.callback(Output("tab-content","children"), Input("tabs","value"))
def render_tab(tab):
    if tab == "landing":
        return landing_layout
    elif tab == "backtest":
        return backtest_layout
    else:
        return fine_layout

from hl_fsm import DEFAULT_PARAMS as FSM_DEFAULTS

@app.callback(
    Output("market-status","children"),
    Output("main-chart","figure"),
    Input("btn-refresh","n_clicks"),
    State("symbol","value"),
    prevent_initial_call=False
)
def update_landing(n, symbol):
    now = datetime.now(timezone.utc)
    if is_market_hours(now):
        status = "Market hours (ET). Showing today's minute data."
        start = datetime.now(US_EASTERN).date()
        end = start
    else:
        status = "Off hours. Showing last full trading day's minute data."
        end = last_trading_day_et()
        start = end

    df = fetch_aggs_range(symbol, 1, "minute", start, end, POLYGON_API_KEY)
    if df is None or df.empty:
        fig = go.Figure()
        fig.update_layout(title="No data")
        return status, fig

    events, marks = detect_hl_patterns(df, FSM_DEFAULTS)

    fig = go.Figure(data=[go.Candlestick(
        x=df['ts'], open=df['open'], high=df['high'], low=df['low'], close=df['close']
    )])
    if marks:
        fig.add_trace(go.Scatter(
            x=[m['ts'] for m in marks],
            y=[m['price'] for m in marks],
            mode="markers+text",
            text=[m['label'] for m in marks],
            textposition="top center"
        ))
    fig.update_layout(xaxis_rangeslider_visible=False)
    return status, fig

@app.callback(
    Output("bt-params-collapse","is_open"),
    Input("btn-bt-params","n_clicks"),
    State("bt-params-collapse","is_open"),
    prevent_initial_call=True
)
def toggle_bt_params(n, is_open): 
    return not is_open

@app.callback(
    Output("bt-summary","children"),
    Output("bt-by-period","data"),
    Output("bt-by-period","columns"),
    Input("btn-bt","n_clicks"),
    State("bt-symbol","value"),
    State("bt-range","start_date"),
    State("bt-range","end_date"),
    State("bt-max_price_increase_above_A0","value"),
    State("bt-price_increase_to_confirm_A0","value"),
    State("bt-max_decrease_below_A0","value"),
    State("bt-price_increase_to_confirm_higher_low","value"),
    State("bt-pattern_time_limit_ms","value"),
    State("bt-take_profit_offset","value"),
    State("bt-stop_loss_offset","value"),
    State("bt-time_to_wait_before_confirm_Ax_ms","value"),
    State("bt-price_increase_from_A2_to_enter_trade","value"),
    State("bt-trade_timeout_ms","value"),
    prevent_initial_call=True
)
def run_backtest(n, symbol, start_date, end_date, *param_values):
    from hl_fsm import DEFAULT_PARAMS as FSM_DEFAULTS
    params = dict(zip(list(FSM_DEFAULTS.keys()), param_values)) if param_values and len(param_values)==len(FSM_DEFAULTS) else FSM_DEFAULTS
    start = datetime.fromisoformat(start_date).date()
    end   = datetime.fromisoformat(end_date).date()

    all_days = pd.date_range(start, end, freq='D')
    df_all = []
    for d in all_days:
        df = fetch_aggs_range(symbol, 1, "minute", d.date(), d.date(), POLYGON_API_KEY)
        if df is None or df.empty:
            continue
        df_all.append(df)
        if engine:
            df.to_sql("candles_minute", engine, if_exists="append", index=False)

    if not df_all:
        return "No data found in range.", [], []

    data = pd.concat(df_all, ignore_index=True)
    occ = run_backtest_on_df(data, params)

    total = len(occ)
    wins = sum(1 for o in occ if o.get('outcome') == 'take_profit')
    win_rate = (wins/total*100) if total else 0.0
    avg_profit = float(np.mean([o.get('profit',0) for o in occ])) if occ else 0.0
    summary = f"Patterns: {total} | Win%: {win_rate:.1f}% | Avg P/L: ${avg_profit:.02f}"

    span_days = (end - start).days + 1
    if span_days <= 31:
        grp = "W"
    elif span_days <= 370:
        grp = "M"
    else:
        grp = "Y"

    if occ:
        df_occ = pd.DataFrame(occ)
        period_map = {'W':'W','M':'M','Y':'Y'}
        df_occ['period'] = pd.to_datetime(df_occ['a0_time']).dt.to_period(period_map[grp]).astype(str)
        agg = df_occ.groupby('period').agg(
            patterns=('a0_time','count'),
            wins=('outcome', lambda s: (s=='take_profit').sum()),
            avg_profit=('profit','mean')
        ).reset_index()
        cols = [{"name":c, "id":c} for c in agg.columns]
        rows = agg.to_dict('records')
    else:
        cols, rows = [], []

    return summary, rows, cols

@app.callback(
    Output("ft-params-collapse","is_open"),
    Input("btn-ft-params","n_clicks"),
    State("ft-params-collapse","is_open"),
    prevent_initial_call=True
)
def toggle_ft_params(n, is_open): 
    return not is_open

@app.callback(
    Output("ft-results","data"),
    Output("ft-results","columns"),
    Input("btn-ft","n_clicks"),
    State("ft-symbol","value"),
    State("ft-range","start_date"),
    State("ft-range","end_date"),
    State("ft-table","data"),
    prevent_initial_call=True
)
def run_fine_tuning(n, symbol, start_date, end_date, rows):
    start = datetime.fromisoformat(start_date).date()
    end   = datetime.fromisoformat(end_date).date()

    all_days = pd.date_range(start, end, freq='D')
    df_all = []
    for d in all_days:
        df = fetch_aggs_range(symbol, 1, "second", d.date(), d.date(), POLYGON_API_KEY)
        if df is None or df.empty:
            continue
        df_all.append(df)
        if engine:
            df.to_sql("candles_second", engine, if_exists="append", index=False)

    if not df_all:
        return [], []

    sec_df = pd.concat(df_all, ignore_index=True)

    results = []
    for r in rows:
        params = {k:r[k] for k in r if k!="set_id"}
        occ = run_backtest_on_df(sec_df, params)
        total = len(occ)
        wins = sum(1 for o in occ if o.get('outcome') == 'take_profit')
        win_rate = (wins/total*100) if total else 0.0
        avg_profit = float(np.mean([o.get('profit',0) for o in occ])) if occ else 0.0
        results.append({
            "set_id": r["set_id"],
            "patterns": total,
            "win_rate_pct": round(win_rate,1),
            "avg_profit": round(avg_profit, 4)
        })
    cols = [{"name":c, "id":c} for c in results[0].keys()] if results else []
    return results, cols

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    app.run_server(host="0.0.0.0", port=port, debug=False)
    # Health check endpoint for DigitalOcean
@server.get("/health")
def health():
    return "ok", 200

