"""
Purpose:
    Handles the generation of both interactive HTML charts (Plotly) for desktop debugging
    and lightweight, styled static images (mplfinance) for mobile Telegram alerts.
"""
import matplotlib
matplotlib.use('Agg') # Force headless rendering to prevent thread crashes
import plotly.graph_objects as go
import mplfinance as mpf
import pandas as pd
from typing import List
from app.structure import Pivot

class Visualizer:
    def _extract_line_coordinates(self, pivots: List[Pivot]) -> List[tuple]:
        """Converts Pivot objects into a list of (timestamp, price) tuples for mplfinance."""
        sorted_pivots = sorted(pivots, key=lambda x: x.timestamp)
        return [(p.timestamp, p.price) for p in sorted_pivots]

    def generate_static_chart(self, df: pd.DataFrame, l1_pivots: List[Pivot], l2_pivots: List[Pivot],
                              save_path: str) -> None:
        """
        Generates a lightweight, mobile-responsive static PNG using mplfinance.
        """
        try:
            # 0. Bulletproof the DataFrame format for mplfinance
            # mplfinance requires Capitalized column names and a strict DatetimeIndex
            df_plot = df.rename(
                columns={'open': 'Open', 'high': 'High', 'low': 'Low', 'close': 'Close', 'volume': 'Volume'})
            df_plot.index = pd.to_datetime(df_plot.index)

            # 1. Build the custom "TradingView" Dark Theme
            mc = mpf.make_marketcolors(
                up='#26a69a', down='#ef5350',
                edge='inherit', wick='inherit', volume='in'
            )
            custom_style = mpf.make_mpf_style(
                marketcolors=mc, gridstyle=':', facecolor='#131722', figcolor='#131722',
                edgecolor='#49535e', gridcolor='#2b323d',
                rc={'text.color': 'white', 'axes.labelcolor': 'white', 'xtick.color': 'white', 'ytick.color': 'white'}
            )

            # 2. Extract and FILTER Pivot Coordinates
            # FIX: We must drop any pivot coordinates that occurred before our 150-candle window,
            # otherwise mplfinance crashes trying to plot an off-chart line.
            min_time = df_plot.index.min()
            max_time = df_plot.index.max()

            def get_filtered_coords(pivots):
                # Ensure pivot timestamps are Pandas Timestamps for direct comparison
                sorted_pivots = sorted(pivots, key=lambda x: pd.to_datetime(x.timestamp))
                valid_coords = []
                for p in sorted_pivots:
                    p_time = pd.to_datetime(p.timestamp)
                    if min_time <= p_time <= max_time:
                        valid_coords.append((p_time, p.price))
                return valid_coords

            l1_coords = get_filtered_coords(l1_pivots)
            l2_coords = get_filtered_coords(l2_pivots)

            # 3. Compile structure lines
            seqs = []
            colors = []
            widths = []

            # We need at least 2 points to draw a line
            if len(l1_coords) > 1:
                seqs.append(l1_coords)
                colors.append('cyan')
                widths.append(1.5)

            if len(l2_coords) > 1:
                seqs.append(l2_coords)
                colors.append('orange')
                widths.append(2.5)

            alines_dict = dict(alines=seqs, colors=colors, linewidths=widths) if seqs else None

            # 4. Generate and save the chart
            mpf.plot(
                df_plot,
                type='candle',
                style=custom_style,
                alines=alines_dict,
                volume=False,
                tight_layout=True,
                figsize=(10, 6),
                savefig=save_path
            )
        except Exception as e:
            import traceback
            print(f"❌ ERROR in Static Visualizer: {e}")
            traceback.print_exc()  # Added this just in case to print full errors to your console

    def generate_html_chart(self, df: pd.DataFrame, l0_pivots: List[Pivot],
                       l1_pivots: List[Pivot], l2_pivots: List[Pivot],
                       save_path: str) -> None:
        """
        Purpose:
            Compiles the OHLC data and Pivot structures into a responsive Plotly chart.
        """
        try:
            l0_sorted = sorted(l0_pivots, key=lambda x: x.timestamp)
            l1_sorted = sorted(l1_pivots, key=lambda x: x.timestamp)
            l2_sorted = sorted(l2_pivots, key=lambda x: x.timestamp)

            fig = go.Figure(data=[go.Candlestick(
                x=df.index,
                open=df['open'], high=df['high'], low=df['low'], close=df['close'],
                name='Price'
            )])

            if l0_sorted:
                fig.add_trace(go.Scatter(
                    x=[p.timestamp for p in l0_sorted], y=[p.price for p in l0_sorted],
                    mode='lines', line=dict(color='gray', width=1.0, dash='dot'), name='Level 0'
                ))

            if l1_sorted:
                fig.add_trace(go.Scatter(
                    x=[p.timestamp for p in l1_sorted], y=[p.price for p in l1_sorted],
                    mode='lines+markers', line=dict(color='cyan', width=1.5),
                    marker=dict(size=8, symbol='circle'), name='Level 1'
                ))

            if l2_sorted:
                fig.add_trace(go.Scatter(
                    x=[p.timestamp for p in l2_sorted], y=[p.price for p in l2_sorted],
                    mode='lines+markers', line=dict(color='orange', width=1.5),
                    marker=dict(size=4, symbol='circle'), name='Level 2'
                ))

            fig.update_layout(
                title="Interactive Structure Debugger",
                yaxis_title="Price",
                xaxis_title="Time",
                template="plotly_dark",
                xaxis_rangeslider_visible=False
            )

            fig.write_html(save_path)
        except Exception as e:
            print(f"❌ ERROR in HTML Visualizer: {e}")