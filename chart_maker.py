import os
import tempfile
import calendar
from datetime import datetime, timedelta
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.font_manager import FontProperties
import market_data


def get_chart_font():
    font_paths = (
        r"C:\Windows\Fonts\msjh.ttc",
        r"C:\Windows\Fonts\mingliu.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
    )
    font_path = next((path for path in font_paths if os.path.isfile(path)), None)
    if font_path is None:
        raise RuntimeError("No supported Traditional Chinese chart font is installed.")
    return FontProperties(fname=font_path)


def generate_technical_charts(btc_history, btc_metrics, oversold_events):
    chart_dir = tempfile.TemporaryDirectory(prefix="crypto_technical_charts_")
    try:
        font = get_chart_font()
        closes = [row["close"] for row in btc_history]
        sma_100 = market_data.calculate_sma_series(closes, 100)
        sma_200 = market_data.calculate_sma_series(closes, 200)
        rsi_series = btc_metrics["rsi_series"]
        chart_color = "#0B0F19"
        panel_color = "#101827"
        grid_color = "#263244"
        price_path = os.path.join(chart_dir.name, "btc_price_structure.png")
        rsi_path = os.path.join(chart_dir.name, "btc_rsi_oversold.png")

        visible_start = max(0, len(btc_history) - 90)
        visible = btc_history[visible_start:]
        dates = [row["date"] for row in visible]
        fig, ax = plt.subplots(figsize=(13, 7.5), dpi=220)
        fig.patch.set_facecolor(chart_color)
        ax.set_facecolor(panel_color)
        ax.plot(
            dates, closes[visible_start:], color="#22D3EE", linewidth=2.2,
            label="BTC 收盤價",
        )
        ax.plot(
            dates, sma_100[visible_start:], color="#FBBF24", linewidth=1.8,
            label="100 日線",
        )
        ax.plot(
            dates, sma_200[visible_start:], color="#A78BFA", linewidth=1.8,
            label="200 日線",
        )
        ax.axhline(
            btc_metrics["resistance"], color="#F87171", linestyle="--",
            linewidth=1.3, label=f"30 日壓力 ${btc_metrics['resistance']:,.0f}",
        )
        ax.axhline(
            btc_metrics["support"], color="#34D399", linestyle="--",
            linewidth=1.3, label=f"30 日支撐 ${btc_metrics['support']:,.0f}",
        )
        ax.set_title(
            "BTC 價格結構與關鍵多空攻防圖", color="#F8FAFC",
            fontsize=19, fontweight="bold", fontproperties=font, pad=16,
        )
        ax.text(
            0.015, 0.97,
            f"現價 ${btc_metrics['spot_price']:,.0f}"
            f"  |  距 90 日高點 {btc_metrics['days_since_high']} 天"
            f"  |  回撤 {btc_metrics['current_drawdown_pct']:.2f}%"
            f"  |  最大回撤 {btc_metrics['maximum_drawdown_pct']:.2f}%",
            transform=ax.transAxes, va="top", color="#F8FAFC", fontsize=10,
            fontproperties=font,
        )
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))
        ax.yaxis.set_major_formatter(
            plt.FuncFormatter(lambda value, _: f"${value:,.0f}")
        )
        ax.tick_params(colors="#CBD5E1", labelsize=9)
        ax.grid(color=grid_color, linewidth=0.65)
        for spine in ax.spines.values():
            spine.set_color("#334155")
        ax.legend(
            loc="upper left", bbox_to_anchor=(0, 0.90), ncol=2,
            facecolor=panel_color, edgecolor="none", labelcolor="#E2E8F0",
            prop=font, fontsize=9,
        )
        fig.tight_layout()
        fig.savefig(price_path, facecolor=chart_color, dpi=220)
        plt.close(fig)

        visible_start = max(0, len(btc_history) - 180)
        visible_history = btc_history[visible_start:]
        visible_rsi = rsi_series[visible_start:]
        rsi_dates = [
            row["date"] for row, value in zip(visible_history, visible_rsi)
            if value is not None
        ]
        rsi_values = [value for value in visible_rsi if value is not None]
        fig, ax = plt.subplots(figsize=(13, 7.5), dpi=220)
        fig.patch.set_facecolor(chart_color)
        ax.set_facecolor(panel_color)
        ax.axhspan(70, 100, color="#EF4444", alpha=0.14)
        ax.axhspan(0, 30, color="#22C55E", alpha=0.14)
        ax.plot(rsi_dates, rsi_values, color="#22D3EE", linewidth=2.0, label="14 日 RSI")
        ax.axhline(70, color="#F87171", linestyle="--", linewidth=1.2, label="70 超買")
        ax.axhline(30, color="#34D399", linestyle="--", linewidth=1.2, label="30 超賣")
        for index, event in enumerate(oversold_events):
            ax.scatter(
                event["low_date"], event["low_rsi"], s=62,
                color="#FBBF24", edgecolors="#0B0F19", linewidths=1.0,
                zorder=5, label="歷史超賣區段低點" if index == 0 else None,
            )
        latest_rsi = next(
            (value for value in reversed(rsi_series) if value is not None), None
        )
        if latest_rsi is not None:
            ax.scatter(
                btc_history[-1]["date"], latest_rsi, s=84, marker="D",
                color="#FB7185", edgecolors="#F8FAFC", linewidths=0.8,
                zorder=6, label=f"最新 RSI {latest_rsi:.1f}",
            )
        ax.set_ylim(0, 100)
        ax.set_title(
            "BTC 14 日 RSI 歷史超賣與底部對比圖", color="#F8FAFC",
            fontsize=19, fontweight="bold", fontproperties=font, pad=16,
        )
        ax.text(
            0.015, 0.97,
            f"近 180 日超賣區段 {len(oversold_events)} 次；"
            "歷史低點不代表未來價格底部",
            transform=ax.transAxes, va="top", color="#F8FAFC", fontsize=10,
            fontproperties=font,
        )
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))
        ax.tick_params(colors="#CBD5E1", labelsize=9)
        ax.grid(color=grid_color, linewidth=0.65)
        for spine in ax.spines.values():
            spine.set_color("#334155")
        ax.legend(
            loc="upper right", facecolor=panel_color, edgecolor="none",
            labelcolor="#E2E8F0", prop=font, fontsize=9,
        )
        fig.tight_layout()
        fig.savefig(rsi_path, facecolor=chart_color, dpi=220)
        plt.close(fig)
        return {"price": price_path, "rsi": rsi_path}, chart_dir
    except Exception:
        chart_dir.cleanup()
        raise


def generate_dca_chart(portfolio, now_tw):
    today = now_tw.date()
    start_date = datetime.fromisoformat(portfolio["start_date"]).date()
    chart_font_path = next(
        (
            path for path in (
                "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
                "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
                "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
                "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
                "C:\\Windows\\Fonts\\msyhbd.ttc",
                "C:\\Windows\\Fonts\\msyh.ttc",
            )
            if os.path.exists(path)
        ),
        None,
    )
    chart_font = (
        FontProperties(fname=chart_font_path, size=14, weight="bold")
        if chart_font_path
        else FontProperties(size=14, weight="bold")
    )
    btc_history = market_data.fetch_yahoo_daily_closes("BTC-USD", start_date, today)
    try:
        fx_history = market_data.fetch_yahoo_daily_closes(
            "USDTWD=X", start_date - timedelta(days=7), today
        )
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        print(f"DCA chart FX history unavailable; using 32.5 where needed: {error}")
        fx_history = {}

    month_ends = []
    year, month = start_date.year, start_date.month
    while (year, month) <= (today.year, today.month):
        month_end = datetime(
            year, month, calendar.monthrange(year, month)[1]
        ).date()
        month_ends.append((f"{year}-{month:02d}", min(month_end, today)))
        if month == 12:
            year, month = year + 1, 1
        else:
            month += 1

    records = portfolio["records"]
    invested_values = []
    market_values = []
    fallback_logged = False
    for month_key, valuation_date in month_ends:
        cumulative_records = [
            record for record in records if record["date"] <= valuation_date.isoformat()
        ]
        invested = sum(float(record["twd_invested"]) for record in cumulative_records)
        accumulated_btc = sum(float(record["btc_bought"]) for record in cumulative_records)
        if month_key == today.strftime("%Y-%m"):
            btc_price = float(portfolio["current_btc_usd"])
            twd_rate = float(portfolio["current_twd_rate"])
        else:
            btc_price = market_data.latest_close_on_or_before(btc_history, valuation_date)
            if btc_price is None or btc_price <= 0:
                raise RuntimeError(
                    f"Missing BTC-USD month-end price for {valuation_date.isoformat()}."
                )
            twd_rate = market_data.latest_close_on_or_before(fx_history, valuation_date)
            if twd_rate is None or twd_rate <= 0:
                if not fallback_logged:
                    print("DCA chart has no recent month-end USDTWD close; using 32.5.")
                    fallback_logged = True
                twd_rate = 32.5
        invested_values.append(invested)
        market_values.append(accumulated_btc * btc_price * twd_rate)

    months = [m for m, _ in month_ends]
    fig, axis = plt.subplots(figsize=(7.2, 5.2), dpi=160)
    fig.patch.set_facecolor("#111827")
    axis.set_facecolor("#0f172a")
    bar_colors = [
        "#f87171" if value >= cost else "#4ade80"
        for value, cost in zip(market_values, invested_values)
    ]
    axis.bar(months, market_values, color=bar_colors, alpha=0.58, label="月底資產總市值")
    axis.plot(
        months, invested_values, color="#fbbf24", marker="o",
        linewidth=2.2, markersize=4, label="累計投入台幣成本",
    )
    axis.set_title(
        "每月投入成本與資產市值",
        color="white", fontsize=18, fontweight="bold",
        fontproperties=chart_font, pad=12,
    )
    axis.set_ylabel(
        "TWD", color="#cbd5e1", fontsize=14, fontweight="bold",
        fontproperties=chart_font,
    )
    axis.tick_params(axis="x", colors="#cbd5e1", labelrotation=45, labelsize=14, pad=6)
    axis.tick_params(axis="y", colors="#cbd5e1", labelsize=14)
    for tick_label in axis.get_xticklabels() + axis.get_yticklabels():
        tick_label.set_fontproperties(chart_font)
    axis.grid(axis="y", color="#334155", linewidth=0.6, alpha=0.8)
    for spine in ("top", "right"):
        axis.spines[spine].set_visible(False)
    for spine in ("bottom", "left"):
        axis.spines[spine].set_color("#475569")
    axis.legend(
        loc="upper left", facecolor="#1e293b", edgecolor="none",
        labelcolor="white", ncol=2, prop=chart_font,
    )
    fig.tight_layout()
    output_path = "chart_dca.png"
    fig.savefig(output_path, facecolor="#111827")
    plt.close(fig)
    return output_path


def generate_dual_chart(cdata, output_path="chart_dual.png"):
    plot_tick_font = FontProperties(size=14, weight="bold")
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(8.4, 5.0), dpi=160)
    fig.patch.set_facecolor("#111827")
    for ax, name, col in [(ax1, "BTC", "#38bdf8"), (ax2, "ETH", "#a78bfa")]:
        cl = cdata[name][0][-100:]
        ma = [sum(cl[max(0, i-19):i+1])/len(cl[max(0, i-19):i+1]) for i in range(len(cl))]
        ax.set_facecolor("#0f172a")
        ax.plot(cl, color=col, linewidth=2.2, label=f"{name} Close")
        ax.plot(ma, color="#fbbf24", linewidth=1.4, linestyle="--", label="20D MA")
        ax.set_title(
            f"{name} 100D Trend", color="white", fontsize=18,
            fontweight="bold",
        )
        ax.tick_params(colors="#94a3b8", labelsize=14)
        for tick_label in ax.get_xticklabels() + ax.get_yticklabels():
            tick_label.set_fontproperties(plot_tick_font)
        ax.grid(color="#1e293b", linewidth=0.6)
        ax.legend(
            facecolor="#1e293b", edgecolor="none", labelcolor="white",
            prop=plot_tick_font,
        )
        for sp in ["top", "right"]:
            ax.spines[sp].set_visible(False)
        for sp in ["bottom", "left"]:
            ax.spines[sp].set_color("#334155")
    plt.tight_layout()
    plt.savefig(output_path, facecolor="#111827")
    plt.close()
    return output_path


def generate_bars_chart(cdata, output_path="chart_bars.png"):
    plot_tick_font = FontProperties(size=14, weight="bold")
    fig, ax = plt.subplots(figsize=(8.4, 5.0), dpi=160)
    fig.patch.set_facecolor("#111827")
    ax.set_facecolor("#0f172a")
    names = list(cdata.keys())
    pcts = [cdata[n][1] for n in names]
    bars = ax.barh(
        names[::-1], pcts[::-1],
        color=["#34d399" if p >= 0 else "#f43f5e" for p in pcts][::-1],
        height=0.55,
    )
    for bar, pct in zip(bars, pcts[::-1]):
        ax.annotate(
            f"{pct:+.2f}%",
            xy=(bar.get_width(), bar.get_y() + bar.get_height() / 2),
            xytext=(6 if pct >= 0 else -6, 0),
            textcoords="offset points",
            ha="left" if pct >= 0 else "right",
            va="center",
            color="white",
            fontsize=14,
            fontweight="bold",
        )
    spread = max(max(pcts) - min(pcts), 1)
    ax.set_xlim(min(0, min(pcts)) - spread * 0.2, max(0, max(pcts)) + spread * 0.2)
    ax.axvline(0, color="#64748b", linewidth=1)
    ax.set_title(
        "24H Price Change (%)", color="white", fontsize=18,
        fontweight="bold",
    )
    ax.tick_params(colors="white", labelsize=14)
    for tick_label in ax.get_xticklabels() + ax.get_yticklabels():
        tick_label.set_fontproperties(plot_tick_font)
    ax.grid(axis="x", color="#1e293b", linewidth=0.6)
    for sp in ["top", "right"]:
        ax.spines[sp].set_visible(False)
    for sp in ["bottom", "left"]:
        ax.spines[sp].set_color("#334155")
    plt.tight_layout()
    plt.savefig(output_path, facecolor="#111827")
    plt.close()
    return output_path
