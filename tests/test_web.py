from __future__ import annotations

import unittest
from unittest.mock import patch

import pandas as pd

from src.web import (
    CLI_ACTIONS,
    build_backfill_command,
    build_backfill_limit_pool_command,
    chart_volume_height,
    chart_volume_scale,
    chart_volume_series,
    chart_volume_value,
    legacy_turnover_volume_multiplier,
    limit_color,
    limit_reason_text,
    load_limit_ladder,
    load_limit_ladder_chart,
    pagination_pages,
    parse_search_page,
    render_limit_ladder,
    render_limit_ladder_chart,
    render_limit_ladder_page,
    render_limit_platform_page,
    render_page,
    render_pattern_candidate_table,
    render_price_volume_svg,
    render_stock_search_form,
    render_stock_search_page,
    render_search_pagination,
    search_stock_candidates,
    split_codes,
    update_config,
    normalized_explicit_volume,
)


class WebConsoleTest(unittest.TestCase):
    def test_update_config_removes_legacy_market_fields_and_scoring(self) -> None:
        config = {
            "app": {},
            "market": {"max_candidates": 10, "min_turnover_amount": 350_000_000},
            "scoring": {"historical_shape": 0.3},
        }
        form = {
            "report_time": ["18:30"],
            "data_ready_time": ["09:00"],
            "skip_non_trading_day": ["on"],
        }

        update_config(config, form)

        self.assertNotIn("max_candidates", config["market"])
        self.assertNotIn("min_turnover_amount", config["market"])
        self.assertNotIn("scoring", config)
        self.assertEqual(config["app"]["report_time"], "18:30")

    def test_split_codes_accepts_comma_space_and_newline(self) -> None:
        self.assertEqual(split_codes("600001, 000001\n002001"), ["600001", "000001", "002001"])

    def test_backfill_command_includes_multiple_stock_codes(self) -> None:
        command = build_backfill_command(
            {
                "backfill_days": ["250"],
                "backfill_sleep": ["1.5"],
                "backfill_stocks": ["600001,000001"],
            }
        )

        self.assertIn("--backfill-stock", command)
        self.assertIn("600001", command)
        self.assertIn("000001", command)

    def test_backfill_limit_pool_command_uses_days_and_sleep(self) -> None:
        command = build_backfill_limit_pool_command(
            {
                "limit_pool_days": ["90"],
                "limit_pool_sleep": ["1.0"],
            }
        )

        self.assertIn("--backfill-limit-pool-days", command)
        self.assertIn("90", command)
        self.assertIn("--limit-pool-sleep", command)
        self.assertIn("1.0", command)

    def test_fetch_button_runs_full_daily_job_without_sending(self) -> None:
        title, args = CLI_ACTIONS["fetch_only"]

        self.assertEqual(title, "完整采集行情")
        self.assertEqual(args, ["--daily-job", "--dry-run"])

    def test_strategy_refresh_button_runs_dry_run_without_sending(self) -> None:
        title, args = CLI_ACTIONS["refresh_patterns"]

        self.assertEqual(title, "更新策略选股")
        self.assertEqual(args, ["--dry-run"])

    def test_price_volume_svg_renders_close_and_volume(self) -> None:
        svg = render_price_volume_svg(
            [
                {"close_price": 10, "turnover": 100},
                {"close_price": 11, "turnover": 180},
                {"close_price": 10.5, "turnover": 120},
            ]
        )

        self.assertIn("<svg", svg)
        self.assertIn("10.50", svg)
        self.assertIn("<rect", svg)

    def test_price_volume_uses_a_share_red_up_green_down(self) -> None:
        svg = render_price_volume_svg(
            [
                {"close_price": 10, "turnover": 100},
                {"close_price": 11, "turnover": 180},
                {"close_price": 10.5, "turnover": 120},
            ]
        )

        self.assertIn('fill="#dc2626"', svg)
        self.assertIn('fill="#16a34a"', svg)

    def test_highest_volume_bar_uses_darker_color(self) -> None:
        bars = [
            {"open_price": 10, "high_price": 10.5, "low_price": 9.8, "close_price": 10, "volume": 100},
            {"open_price": 10, "high_price": 11.2, "low_price": 9.9, "close_price": 11, "volume": 300},
            {"open_price": 11, "high_price": 11.1, "low_price": 10.2, "close_price": 10.5, "volume": 200},
        ]

        line_svg = render_price_volume_svg(bars, "line")
        candle_svg = render_price_volume_svg(bars, "candle")

        self.assertIn('fill="#991b1b" opacity="0.95"', line_svg)
        self.assertIn('fill="#991b1b" opacity="0.95"', candle_svg)

    def test_price_volume_prefers_daily_bar_volume_when_turnover_missing(self) -> None:
        svg = render_price_volume_svg(
            [
                {"close_price": 10, "volume": 100, "turnover": None},
                {"close_price": 11, "volume": 200, "turnover": None},
                {"close_price": 10.5, "volume": 300, "turnover": None},
            ]
        )

        self.assertEqual(chart_volume_value({"volume": 123, "turnover": None}), 123.0)
        self.assertIn('height="18.3"', svg)
        self.assertIn('height="36.7"', svg)
        self.assertIn('height="55.0"', svg)

    def test_price_volume_converts_legacy_turnover_hands_to_shares(self) -> None:
        bars = [
            {"close_price": 10, "volume": None, "turnover": 2_000_000},
            {"close_price": 11, "volume": None, "turnover": 3_000_000},
            {"close_price": 10.5, "volume": 220_000_000, "turnover": 2_500_000_000},
        ]

        self.assertEqual(legacy_turnover_volume_multiplier([0.0, 0.0, 220_000_000.0], [2_000_000.0, 3_000_000.0, 2_500_000_000.0]), 100.0)
        self.assertEqual(chart_volume_series(bars), [200_000_000.0, 300_000_000.0, 220_000_000.0])

    def test_price_volume_normalizes_explicit_hands_and_shares(self) -> None:
        in_hands = {
            "close_price": 10,
            "volume": 1_000_000,
            "turnover": 1_000_000_000,
        }
        in_shares = {
            "close_price": 10,
            "volume": 100_000_000,
            "turnover": 1_000_000_000,
        }

        self.assertEqual(normalized_explicit_volume(in_hands), 100_000_000)
        self.assertEqual(normalized_explicit_volume(in_shares), 100_000_000)

    def test_price_volume_scale_keeps_smaller_bars_visible_with_extreme_spikes(self) -> None:
        values = [100.0] * 30 + [20000.0, 50000.0]
        scale = chart_volume_scale(values)
        smaller_spike = chart_volume_height(20000.0, scale, 55)
        largest_spike = chart_volume_height(50000.0, scale, 55)

        self.assertEqual(scale, 50000.0)
        self.assertAlmostEqual(chart_volume_height(100.0, scale, 55), 0.11)
        self.assertLess(smaller_spike, largest_spike)
        self.assertEqual(largest_spike, 55.0)

    def test_price_volume_keeps_recent_high_volume_bars_distinct(self) -> None:
        scale = chart_volume_scale([1000.0, 30000.0, 40000.0, 50000.0])
        heights = [chart_volume_height(value, scale, 55) for value in [30000.0, 40000.0, 50000.0]]

        self.assertLess(heights[0], heights[1])
        self.assertLess(heights[1], heights[2])

    def test_pattern_candidate_table_renders_limit_platform_section(self) -> None:
        content = render_pattern_candidate_table(
            [
                {
                    "code": "002421",
                    "name": "达实智能",
                    "stage": "收复确认",
                    "score": 88,
                    "close_price": 10.8,
                    "turnover_rate": 6.5,
                    "total_market_cap": 5_000_000_000,
                    "limit_reason": "机器人 · 获得大额订单",
                    "reasons": [
                        "前段形态: 7天6板",
                        "巨量: 最大换手 暂缺，成交额放大 7.50x",
                        "平台: 日均振幅 10%",
                    ],
                }
            ]
        )

        self.assertIn("002421", content)
        self.assertIn("收复确认", content)
        self.assertIn("7天6板", content)
        self.assertIn("10.80", content)
        self.assertIn("6.5%", content)
        self.assertIn("50.0亿", content)
        self.assertNotIn("涨停原因", content)
        self.assertNotIn("机器人 · 获得大额订单", content)
        self.assertNotIn("巨量", content)
        self.assertNotIn("最大换手", content)
        self.assertNotIn("成交额放大", content)

        self.assertIn('class="reason-details"', content)
        self.assertIn('class="reason-preview"', content)
        self.assertIn('class="reason-toggle"', content)
        self.assertIn("compact-pattern-table", content)
        self.assertIn('data-chart-template="hover-chart-pattern-002421"', content)
        self.assertIn('id="hover-chart-pattern-002421"', content)

    def test_stock_name_search_uses_total_and_page_offset(self) -> None:
        class Store:
            queries: list[tuple[str, dict[str, object]]] = []

            def _read_df(self, sql: str, params: dict[str, object]) -> pd.DataFrame:
                self.queries.append((sql, params.copy()))
                if "COUNT(*) AS total" in sql:
                    return pd.DataFrame([{"total": 25}])
                return pd.DataFrame(
                    [
                        {
                            "code": f"600{i:03d}",
                            "name": f"中样本{i}",
                            "latest_trade_date": __import__("datetime").date(2026, 6, 18),
                        }
                        for i in range(12, 24)
                    ]
                )

        store = Store()
        results, total, page = search_stock_candidates(  # type: ignore[arg-type]
            store,
            "中",
            __import__("datetime").date(2026, 6, 18),
            page=2,
        )

        self.assertEqual(len(results), 12)
        self.assertEqual(total, 25)
        self.assertEqual(page, 2)
        self.assertEqual(store.queries[-1][1]["page_size"], 12)
        self.assertEqual(store.queries[-1][1]["offset"], 12)
        self.assertIn("LIMIT :page_size OFFSET :offset", store.queries[-1][0])
        self.assertTrue(all("sb.market <> 'beijing'" in sql for sql, _ in store.queries))
        self.assertTrue(all("sb.name NOT LIKE :delisted_name" in sql for sql, _ in store.queries))
        self.assertTrue(all(params["delisted_name"] == "%退%" for _, params in store.queries))

    def test_exact_stock_search_excludes_beijing_market_and_legacy_codes(self) -> None:
        class Store:
            queries: list[str] = []

            def _read_df(self, sql: str, params: dict[str, object]) -> pd.DataFrame:
                self.queries.append(sql)
                return pd.DataFrame()

        store = Store()
        results, total, page = search_stock_candidates(  # type: ignore[arg-type]
            store,
            "920000",
            __import__("datetime").date(2026, 6, 18),
        )

        self.assertEqual((results, total, page), ([], 0, 1))
        self.assertIn("sb.market <> 'beijing'", store.queries[0])
        self.assertIn("db.code NOT REGEXP '^(43|83|87|92)'", store.queries[1])
        self.assertIn("sb.name LIKE :delisted_name", store.queries[1])

    def test_exact_stock_search_does_not_fall_back_to_delisted_daily_bars(self) -> None:
        class Store:
            queries: list[tuple[str, dict[str, object]]] = []

            def _read_df(self, sql: str, params: dict[str, object]) -> pd.DataFrame:
                self.queries.append((sql, params.copy()))
                return pd.DataFrame()

        store = Store()
        results, total, _ = search_stock_candidates(  # type: ignore[arg-type]
            store,
            "300029",
            __import__("datetime").date(2026, 6, 18),
        )

        self.assertEqual((results, total), ([], 0))
        self.assertIn("sb.name NOT LIKE :delisted_name", store.queries[0][0])
        self.assertIn("COALESCE(db.name, '') NOT LIKE :delisted_name", store.queries[1][0])
        self.assertEqual(store.queries[1][1]["delisted_name"], "%退%")

    def test_stock_name_search_clamps_page_to_last_page(self) -> None:
        class Store:
            last_params: dict[str, object] = {}

            def _read_df(self, sql: str, params: dict[str, object]) -> pd.DataFrame:
                if "COUNT(*) AS total" in sql:
                    return pd.DataFrame([{"total": 25}])
                self.last_params = params.copy()
                return pd.DataFrame(
                    [
                        {
                            "code": "600024",
                            "name": "中样本24",
                            "latest_trade_date": __import__("datetime").date(2026, 6, 18),
                        }
                    ]
                )

        store = Store()
        _, _, page = search_stock_candidates(  # type: ignore[arg-type]
            store,
            "中",
            __import__("datetime").date(2026, 6, 18),
            page=99,
        )

        self.assertEqual(page, 3)
        self.assertEqual(store.last_params["offset"], 24)

    def test_search_pagination_uses_standalone_search_route(self) -> None:
        content = render_search_pagination(
            "中",
            2,
            25,
            "line",
        )

        self.assertIn("共 25 只 · 第 2/3 页", content)
        self.assertIn("stock_query=%E4%B8%AD", content)
        self.assertIn("search_page=1", content)
        self.assertIn("search_page=3", content)
        self.assertIn("chart_style=line", content)
        self.assertIn('href="/search?', content)
        self.assertNotIn("ladder_date=", content)
        self.assertIn('aria-current="page">2</span>', content)

    def test_search_form_uses_standalone_page(self) -> None:
        form = render_stock_search_form("中", "line")
        self.assertIn('action="/search"', form)

    def test_console_uses_responsive_workstation_layout(self) -> None:
        content = render_page({"app": {}, "market": {}})

        self.assertIn('name="viewport"', content)
        self.assertIn("TRADE MSG / OPERATIONS", content)
        self.assertIn('class="link-button active" href="/"', content)
        self.assertIn('href="/search">搜索日 K</a>', content)
        self.assertIn('href="/ladder">连板天梯</a>', content)
        self.assertIn('href="/patterns">策略中心</a>', content)
        self.assertNotIn('href="/report"', content)
        self.assertEqual(content.count('class="link-button'), 4)
        self.assertIn('class="base-config"', content)
        self.assertIn('class="config-actions"', content)
        self.assertIn('class="manual-actions"', content)
        self.assertIn('class="job-console"', content)
        self.assertIn('value="refresh_patterns">更新策略选股</button>', content)

    def test_ladder_page_renders_standalone_date_navigation(self) -> None:
        trade_date = __import__("datetime").date(2026, 6, 18)
        payload = {
            "latest_date": trade_date,
            "ladder_date": trade_date,
            "ladder_date_options": [trade_date],
            "limit_ladder": [
                {
                    "code": "600001",
                    "name": "样本",
                    "max_limit_up_days": 3,
                    "industry": "机器人",
                    "reason": "人工智能",
                    "reached_at": trade_date,
                }
            ],
            "limit_ladder_chart": [
                {"trade_date": "2026-06-17", "max_limit_up_days": 2, "names": "前日"},
                {"trade_date": "2026-06-18", "max_limit_up_days": 3, "names": "样本"},
            ],
        }
        with patch("src.web.load_limit_ladder_page_payload", return_value=payload):
            content = render_limit_ladder_page()

        self.assertIn('class="link-button active" href="/ladder"', content)
        self.assertIn("location.href='/ladder?ladder_date='", content)
        self.assertIn('class="ladder-table"', content)
        self.assertIn('class="metric-strip"', content)
        self.assertIn('class="table-scroll"', content)

    def test_limit_platform_page_renders_standalone_candidates(self) -> None:
        trade_date = __import__("datetime").date(2026, 6, 18)
        candidate = {
            "code": "600001",
            "name": "样本",
            "score": 88,
            "stage": "收复确认",
            "close_price": 10.8,
            "volume": 12_345_678,
            "turnover": 500_000_000,
            "turnover_rate": 6.5,
            "total_market_cap": 5_000_000_000,
            "limit_reason": "机器人 · 获得大额订单",
            "strategy_tags": ["连板平台洗盘", "收复确认"],
            "reasons": ["平台缩量", "巨量: 最大换手 暂缺，成交额放大 7.50x"],
            "trigger_text": "放量突破",
            "invalidation_text": "跌破平台",
        }
        payload = {
            "trade_date": trade_date,
            "pattern_candidates": [candidate],
            "bars": {
                "600001": [
                    {"open_price": 10, "high_price": 11, "low_price": 9.8, "close_price": 10.8, "volume": 100}
                ]
            },
        }
        with patch("src.web.load_limit_platform_payload", return_value=payload):
            content = render_limit_platform_page("line")

        self.assertIn('class="link-button active" href="/patterns"', content)
        self.assertIn("<h1>策略中心</h1>", content)
        self.assertIn("location.href='/patterns?chart_style='", content)
        self.assertIn('action="/" class="inline-action-form"', content)
        self.assertIn('value="refresh_patterns" type="submit">更新策略选股</button>', content)
        self.assertNotIn('<select class="filter-select" name="pattern_sort"', content)
        self.assertIn('class="table-sort-link active"', content)
        self.assertIn("pattern_sort=price_desc", content)
        self.assertIn("股价", content)
        self.assertIn("换手率", content)
        self.assertIn("成交量", content)
        self.assertIn("成交额", content)
        self.assertIn("总市值", content)
        self.assertIn('class="limit-reason-note"', content)
        self.assertIn('class="limit-reason-tooltip"', content)
        self.assertIn("涨停原因", content)
        self.assertIn("机器人 · 获得大额订单", content)
        self.assertIn("compact-pattern-table", content)
        self.assertIn('id="hover-chart-panel"', content)
        self.assertIn('data-chart-template="hover-chart-pattern-600001"', content)
        self.assertNotIn("最大换手", content)
        self.assertNotIn("成交额放大", content)
        self.assertNotIn('<p class="hint">依据：', content)
        self.assertIn('class="stock-card"', content)
        self.assertIn('class="metric-strip"', content)
        self.assertIn('class="table-scroll"', content)

    def test_limit_platform_page_filters_and_limits_candidates(self) -> None:
        trade_date = __import__("datetime").date(2026, 6, 18)
        candidates = [
            {
                "code": f"600{i:03d}",
                "name": f"样本{i}",
                "score": 90 - i,
                "stage": "收复确认" if i % 2 == 0 else "观察",
                "close_price": 30 - i,
                "turnover_rate": 5 + i,
                "total_market_cap": 10_000_000_000 + i,
                "strategy_tags": ["连板平台洗盘"],
                "reasons": [f"依据{i}"],
                "trigger_text": "放量突破",
                "invalidation_text": "跌破平台",
            }
            for i in range(13)
        ]
        payload = {
            "trade_date": trade_date,
            "pattern_candidates": candidates,
            "bars": {
                item["code"]: [{"open_price": 10, "high_price": 11, "low_price": 9.8, "close_price": 10.8}]
                for item in candidates
            },
        }
        with patch("src.web.load_limit_platform_payload", return_value=payload):
            default_content = render_limit_platform_page("line")
            filtered_content = render_limit_platform_page("line", "600012", "", "0", "all")
            sorted_content = render_limit_platform_page("line", "", "", "0", "all", "price_asc")

        self.assertIn("显示 12 / 13 只", default_content)
        self.assertIn("展开全部 13 只", default_content)
        self.assertIn("display_count=all", default_content)
        self.assertNotIn("600012", default_content)
        self.assertIn("600012", filtered_content)
        self.assertIn("pattern_query=600012", filtered_content)
        self.assertIn("display_count=all", filtered_content)
        self.assertLess(sorted_content.index("600012"), sorted_content.index("600000"))
        self.assertIn("pattern_sort=price_asc", sorted_content)
        self.assertIn("收起至 12 只", sorted_content)
        self.assertIn('aria-sort="ascending"', sorted_content)
        self.assertIn('name="pattern_sort" value="price_asc"', sorted_content)

    def test_standalone_search_page_renders_active_navigation(self) -> None:
        payload = {
            "trade_date": __import__("datetime").date(2026, 6, 18),
            "search_query": "中",
            "search_results": [],
            "search_page": 1,
            "search_total": 0,
            "search_bars": {},
        }
        with patch("src.web.load_stock_search_payload", return_value=payload):
            content = render_stock_search_page("line", "中", "1")

        self.assertIn('class="link-button active" href="/search"', content)
        self.assertIn('action="/search"', content)
        self.assertIn("数据截至 2026-06-18", content)

    def test_search_page_helpers_handle_invalid_and_large_ranges(self) -> None:
        self.assertEqual(parse_search_page("bad"), 1)
        self.assertEqual(parse_search_page("0"), 1)
        self.assertEqual(pagination_pages(10, 20), [1, 8, 9, 10, 11, 12, 20])

    def test_candle_chart_renders_candles(self) -> None:
        svg = render_price_volume_svg(
            [
                {"open_price": 10, "high_price": 11, "low_price": 9.5, "close_price": 10.8, "turnover": 100},
                {"open_price": 11, "high_price": 11.2, "low_price": 10.1, "close_price": 10.3, "turnover": 180},
            ],
            "candle",
        )

        self.assertIn("蜡烛K线图和成交量", svg)
        self.assertIn("<line", svg)
        self.assertIn('fill="#dc2626"', svg)
        self.assertIn('fill="#16a34a"', svg)

    def test_short_price_charts_keep_fixed_spacing_and_align_left(self) -> None:
        bars = [
            {"open_price": 9.8, "high_price": 10.2, "low_price": 9.7, "close_price": 10.0, "volume": 100},
            {"open_price": 10.0, "high_price": 11.2, "low_price": 9.9, "close_price": 11.0, "volume": 200},
            {"open_price": 11.0, "high_price": 12.2, "low_price": 10.9, "close_price": 12.0, "volume": 300},
        ]

        line_svg = render_price_volume_svg(bars, "line")
        candle_svg = render_price_volume_svg(bars, "candle")

        self.assertIn('points="0.0,138.0 6.6,76.0 13.2,14.0"', line_svg)
        self.assertIn('x1="3.2"', candle_svg)
        self.assertIn('x1="9.8"', candle_svg)

    def test_limit_ladder_renders_highest_streak_rows(self) -> None:
        content = render_limit_ladder(
            [
                {
                    "code": "600001",
                    "name": "Sample A",
                    "max_limit_up_days": 5,
                    "industry": "机器人",
                    "reason": "机器人+人工智能",
                    "reached_at": "2026-05-29",
                },
                {"code": "000001", "name": "Sample B", "max_limit_up_days": 3, "reached_at": "2026-05-20"},
            ]
        )

        self.assertIn("600001", content)
        self.assertIn("5", content)
        self.assertIn("机器人+人工智能", content)
        self.assertIn("板块", content)
        self.assertIn("ladder-table", content)
        self.assertIn("ladder-badge", content)

    def test_limit_reason_text_returns_empty_when_missing(self) -> None:
        self.assertEqual(limit_reason_text({"industry": "机器人", "reason": None}), "")
        self.assertEqual(limit_reason_text({}), "")

    def test_limit_ladder_defaults_to_top_ten_with_expand(self) -> None:
        items = [
            {"code": f"600{i:03d}", "name": f"Sample {i}", "max_limit_up_days": 3, "reached_at": "2026-05-29"}
            for i in range(12)
        ]

        content = render_limit_ladder(items)

        self.assertIn("展开全部 12 只", content)
        self.assertEqual(content.count("<table class=\"ladder-table\">"), 1)
        self.assertIn("extra-row", content)
        self.assertIn("ladder-toggle", content)
        self.assertIn("ladder-date-select", content)
        self.assertIn("600009", content)
        self.assertIn("600011", content)

    def test_limit_ladder_date_filter_lives_in_date_header(self) -> None:
        day = __import__("datetime").date(2026, 5, 29)
        previous = __import__("datetime").date(2026, 5, 28)
        content = render_limit_ladder(
            [{"code": "600001", "name": "Sample", "max_limit_up_days": 3, "reached_at": day}],
            day,
            day,
            [day, previous],
        )

        self.assertIn("<th><select", content)
        self.assertIn("ladder-date-select", content)
        self.assertIn("report-day selected-day", content)
        self.assertNotIn("trade-date-badge", content)

    def test_limit_ladder_uses_red_depth_palette(self) -> None:
        self.assertEqual(limit_color(2), "#fee2e2")
        self.assertEqual(limit_color(10), "#7f1d1d")

    def test_limit_ladder_chart_renders_connected_points(self) -> None:
        content = render_limit_ladder_chart(
            [
                {"trade_date": "2026-05-27", "max_limit_up_days": 3, "names": "A"},
                {"trade_date": "2026-05-28", "max_limit_up_days": 5, "names": "B"},
                {"trade_date": "2026-05-29", "max_limit_up_days": 4, "names": "C"},
            ]
        )

        self.assertIn("<polyline", content)
        self.assertIn("5板", content)
        self.assertIn("10日连板天梯图", content)

    def test_limit_ladder_chart_stacks_multiple_leaders_above_point(self) -> None:
        content = render_limit_ladder_chart(
            [
                {"trade_date": "2026-05-26", "max_limit_up_days": 3, "names": "A"},
                {
                    "trade_date": "2026-05-27",
                    "max_limit_up_days": 8,
                    "leaders": [
                        {"code": "600001", "name": "一号股份"},
                        {"code": "600002", "name": "二号股份"},
                        {"code": "600003", "name": "三号股份"},
                    ],
                },
                {"trade_date": "2026-05-28", "max_limit_up_days": 4, "names": "C"},
            ]
        )

        self.assertIn("一号股份", content)
        self.assertIn("二号股份", content)
        self.assertIn("三号股份", content)
        self.assertNotIn("<rect x=", content)
        self.assertIn('font-size="9"', content)

    def test_limit_ladder_chart_renders_every_co_leader(self) -> None:
        leaders = [
            {"code": f"600{i:03d}", "name": f"并列{i}"}
            for i in range(10)
        ]
        content = render_limit_ladder_chart(
            [
                {"trade_date": "2026-06-16", "max_limit_up_days": 2, "names": "前日"},
                {"trade_date": "2026-06-17", "max_limit_up_days": 3, "leaders": leaders},
            ]
        )

        for leader in leaders:
            self.assertIn(leader["name"], content)
        self.assertIn('viewBox="0 0 1120 516"', content)

    def test_load_limit_ladder_chart_keeps_every_highest_streak_stock(self) -> None:
        class Store:
            calls = 0

            def _read_df(self, sql: str, params: dict[str, object]) -> pd.DataFrame:
                self.calls += 1
                if self.calls == 1:
                    return pd.DataFrame(
                        [{"trade_date": __import__("datetime").date(2026, 6, day)} for day in (16, 17)]
                    )
                return pd.DataFrame(
                    [
                        {
                            "trade_date": __import__("datetime").date(2026, 6, 17),
                            "code": f"600{i:03d}",
                            "name": f"并列{i}",
                            "limit_up_days": 3,
                        }
                        for i in range(10)
                    ]
                )

        rows = load_limit_ladder_chart(  # type: ignore[arg-type]
            Store(), __import__("datetime").date(2026, 6, 17)
        )

        self.assertEqual(len(rows[0]["leaders"]), 10)
        self.assertIn("并列9", rows[0]["names"])

    def test_limit_ladder_chart_labels_are_centered_on_edge_points(self) -> None:
        content = render_limit_ladder_chart(
            [
                {"trade_date": "2026-05-26", "max_limit_up_days": 8, "names": "左边界"},
                {"trade_date": "2026-05-27", "max_limit_up_days": 3, "names": "中间"},
                {"trade_date": "2026-05-28", "max_limit_up_days": 9, "names": "右边界"},
            ]
        )

        self.assertIn('cx="140.0"', content)
        self.assertIn('x="140.0"', content)
        self.assertIn('cx="980.0"', content)
        self.assertIn('x="980.0"', content)

    def test_load_limit_ladder_queries_only_target_trade_date(self) -> None:
        class Store:
            sql = ""

            def _read_df(self, sql: str, params: dict[str, object]) -> pd.DataFrame:
                self.sql = sql
                return pd.DataFrame(
                    [
                        {
                            "code": "600001",
                            "name": "样本",
                            "max_limit_up_days": 3,
                            "industry": "机器人",
                            "reason": "机器人+人工智能",
                            "reached_at": params["trade_date"],
                        }
                    ]
                )

        store = Store()
        rows = load_limit_ladder(store, __import__("datetime").date(2026, 5, 29))  # type: ignore[arg-type]

        self.assertEqual(rows[0]["code"], "600001")
        self.assertIn("lp.trade_date = :trade_date", store.sql)
        self.assertNotIn("lp.trade_date <= :trade_date", store.sql)


if __name__ == "__main__":
    unittest.main()
