# pages/11_app_high.py
import streamlit as st
import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text
import urllib.parse
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime

# ========== 1. 頁面配置 ==========
st.set_page_config(
    page_title="StockRevenueLab | 最高價趨勢雷達",
    page_icon="🚀",
    layout="wide"
)

# 自定義 CSS
st.markdown("""
    <style>
    .main { background-color: #f8f9fa; }
    .stMetric { border-left: 5px solid #ff4b4b; background-color: white; padding: 10px; border-radius: 5px; }
    .stat-card { background: white; padding: 15px; border-radius: 10px; box-shadow: 0 2px 5px rgba(0,0,0,0.1); margin: 5px; }
    .counter-badge { background: linear-gradient(45deg, #FF6B6B, #FF8E53); color: white; padding: 5px 15px; border-radius: 20px; font-weight: bold; }
    .warning-box { background-color: #fff3cd; border: 1px solid #ffc107; border-radius: 5px; padding: 15px; margin: 10px 0; }
    </style>
""", unsafe_allow_html=True)

# ========== 2. 安全資料庫連線 ==========
@st.cache_resource
def get_engine():
    try:
        DB_PASSWORD = st.secrets["DB_PASSWORD"]
        PROJECT_REF = st.secrets["PROJECT_REF"]
        POOLER_HOST = st.secrets["POOLER_HOST"]
        encoded_password = urllib.parse.quote_plus(DB_PASSWORD)
        connection_string = f"postgresql://postgres.{PROJECT_REF}:{encoded_password}@{POOLER_HOST}:5432/postgres?sslmode=require"
        return create_engine(connection_string)
    except Exception as e:
        st.error("❌ 資料庫連線失敗，請檢查 Streamlit Secrets 設定。")
        st.stop()

# ========== 3. 側邊欄設定 ==========
st.sidebar.header("🚀 最高價版本說明")
st.sidebar.info("""
**版本特色**：
- 使用「年度最高價」計算潛在最大漲幅
- 顯示股價可能到達的極限位置
- 適合追求「極限目標價」的投資者
""")

st.sidebar.warning("""
**注意事項**：
此版本使用「年度最高價」計算，代表：
1. **樂觀情境**：顯示股價可能達到的最高點
2. **非實際報酬**：需要精準賣在最高點才能實現
3. **波動較大**：數值通常比收盤價版本更高
""")

# 核心變數定義區
st.sidebar.header("🔬 研究條件篩選")
target_year = st.sidebar.selectbox("分析年度", [str(y) for y in range(2025, 2019, -1)], index=1)
metric_choice = st.sidebar.radio("成長指標", ["年增率 (YoY)", "月增率 (MoM)"], help="YoY看長期趨勢，MoM看短期爆發")
target_col = "yoy_pct" if metric_choice == "年增率 (YoY)" else "mom_pct"

stat_methods = [
    "中位數 (排除極端值)",
    "平均值 (含極端值)", 
    "標準差 (波動程度)",
    "變異係數 (相對波動)",
    "偏度 (分佈形狀)",
    "峰度 (尾部厚度)",
    "四分位距 (離散程度)",
    "正樣本比例"
]
stat_method = st.sidebar.selectbox("統計指標模式", stat_methods, index=0)

# ========== 4. 數據抓取引擎（最高價版本）==========
@st.cache_data(ttl=3600)
def fetch_heatmap_data_high(year, metric_col, stat_method):
    """最高價版本：使用 year_high 計算年度最大漲幅"""
    engine = get_engine()
    minguo_year = int(year) - 1911
    prev_minguo_year = minguo_year - 1
    
    # 根據統計方法選擇聚合函數（與原版相同）
    if stat_method == "中位數 (排除極端值)":
        agg_func = f"percentile_cont(0.5) WITHIN GROUP (ORDER BY m.{metric_col})"
        stat_label = "中位數"
    elif stat_method == "平均值 (含極端值)":
        agg_func = f"AVG(m.{metric_col})"
        stat_label = "平均值"
    elif stat_method == "標準差 (波動程度)":
        agg_func = f"STDDEV(m.{metric_col})"
        stat_label = "標準差"
    elif stat_method == "變異係數 (相對波動)":
        agg_func = f"CASE WHEN AVG(m.{metric_col}) = 0 THEN 0 ELSE (STDDEV(m.{metric_col}) / ABS(AVG(m.{metric_col}))) * 100 END"
        stat_label = "變異係數%"
    elif stat_method == "偏度 (分佈形狀)":
        agg_func = f"""
        CASE WHEN STDDEV(m.{metric_col}) = 0 THEN 0 
             ELSE (AVG(POWER((m.{metric_col} - AVG(m.{metric_col}))/NULLIF(STDDEV(m.{metric_col}),0), 3))) 
        END
        """
        stat_label = "偏度"
    elif stat_method == "峰度 (尾部厚度)":
        agg_func = f"""
        CASE WHEN STDDEV(m.{metric_col}) = 0 THEN 0 
             ELSE (AVG(POWER((m.{metric_col} - AVG(m.{metric_col}))/NULLIF(STDDEV(m.{metric_col}),0), 4)) - 3) 
        END
        """
        stat_label = "峰度"
    elif stat_method == "四分位距 (離散程度)":
        agg_func = f"percentile_cont(0.75) WITHIN GROUP (ORDER BY m.{metric_col}) - percentile_cont(0.25) WITHIN GROUP (ORDER BY m.{metric_col})"
        stat_label = "四分位距"
    elif stat_method == "正樣本比例":
        agg_func = f"SUM(CASE WHEN m.{metric_col} > 0 THEN 1 ELSE 0 END) * 100.0 / COUNT(*)"
        stat_label = "正增長比例%"
    else:
        agg_func = f"AVG(m.{metric_col})"
        stat_label = "平均值"
    
    # 🔥 關鍵修改：使用 year_high 計算年度最大漲幅
    query = f"""
    WITH annual_bins AS (
        SELECT 
            symbol,
            -- 使用最高價計算最大潛在漲幅
            ((year_high - year_open) / year_open) * 100 AS annual_max_return,
            CASE 
                -- 注意：最高價版本沒有負數區間，因為最高價一定 >= 開盤價
                WHEN ((year_high - year_open) / year_open) * 100 < 100 THEN '01. 上漲0-100%'
                WHEN ((year_high - year_open) / year_open) * 100 < 200 THEN '02. 上漲100-200%'
                WHEN ((year_high - year_open) / year_open) * 100 < 300 THEN '03. 上漲200-300%'
                WHEN ((year_high - year_open) / year_open) * 100 < 400 THEN '04. 上漲300-400%'
                WHEN ((year_high - year_open) / year_open) * 100 < 500 THEN '05. 上漲400-500%'
                WHEN ((year_high - year_open) / year_open) * 100 < 600 THEN '06. 上漲500-600%'
                WHEN ((year_high - year_open) / year_open) * 100 < 700 THEN '07. 上漲600-700%'
                WHEN ((year_high - year_open) / year_open) * 100 < 800 THEN '08. 上漲700-800%'
                WHEN ((year_high - year_open) / year_open) * 100 < 900 THEN '09. 上漲800-900%'
                WHEN ((year_high - year_open) / year_open) * 100 < 1000 THEN '10. 上漲900-1000%'
                ELSE '11. 上漲1000%以上'
            END AS return_bin,
            -- 為了分組排序，新增一個順序欄位
            CASE 
                WHEN ((year_high - year_open) / year_open) * 100 < 100 THEN 1
                WHEN ((year_high - year_open) / year_open) * 100 < 200 THEN 2
                WHEN ((year_high - year_open) / year_open) * 100 < 300 THEN 3
                WHEN ((year_high - year_open) / year_open) * 100 < 400 THEN 4
                WHEN ((year_high - year_open) / year_open) * 100 < 500 THEN 5
                WHEN ((year_high - year_open) / year_open) * 100 < 600 THEN 6
                WHEN ((year_high - year_open) / year_open) * 100 < 700 THEN 7
                WHEN ((year_high - year_open) / year_open) * 100 < 800 THEN 8
                WHEN ((year_high - year_open) / year_open) * 100 < 900 THEN 9
                WHEN ((year_high - year_open) / year_open) * 100 < 1000 THEN 10
                ELSE 11
            END AS bin_order
        FROM stock_annual_k
        WHERE year = '{year}'
        -- 過濾掉數據不完整的記錄
        AND year_open IS NOT NULL AND year_high IS NOT NULL
        AND year_open > 0
    ),
    monthly_stats AS (
        SELECT stock_id, report_month, {metric_col} 
        FROM monthly_revenue
        WHERE report_month = '{prev_minguo_year}_12'  -- 去年12月
           OR (report_month LIKE '{minguo_year}_%' 
               AND report_month < '{minguo_year}_12'  -- 排除當年12月
               AND LENGTH(report_month) <= 7)
    )
    
    SELECT 
        b.return_bin,
        b.bin_order,
        m.report_month,
        {agg_func} as val,
        COUNT(DISTINCT b.symbol) as stock_count,
        COUNT(m.{metric_col}) as data_points,
        AVG(b.annual_max_return) as avg_max_return  -- 計算該區間的平均最大漲幅
    FROM annual_bins b
    JOIN monthly_stats m ON SPLIT_PART(b.symbol, '.', 1) = m.stock_id
    WHERE m.{metric_col} IS NOT NULL
    GROUP BY b.return_bin, b.bin_order, m.report_month
    ORDER BY b.bin_order, m.report_month;
    """
    
    with engine.connect() as conn:
        df = pd.read_sql_query(text(query), conn)
        df['stat_method'] = stat_method
        df['stat_label'] = stat_label
        df = df.sort_values(['bin_order', 'report_month'])
        return df

# ========== 5. 統計摘要數據抓取（最高價版本）==========
@st.cache_data(ttl=3600)
def fetch_stat_summary_high(year, metric_col):
    """最高價版本統計摘要"""
    engine = get_engine()
    minguo_year = int(year) - 1911
    prev_minguo_year = minguo_year - 1
    
    query = f"""
    WITH annual_bins AS (
        SELECT 
            symbol,
            -- 使用最高價計算最大潛在漲幅
            ((year_high - year_open) / year_open) * 100 AS annual_max_return,
            CASE 
                WHEN ((year_high - year_open) / year_open) * 100 < 100 THEN '01. 上漲0-100%'
                WHEN ((year_high - year_open) / year_open) * 100 < 200 THEN '02. 上漲100-200%'
                WHEN ((year_high - year_open) / year_open) * 100 < 300 THEN '03. 上漲200-300%'
                WHEN ((year_high - year_open) / year_open) * 100 < 400 THEN '04. 上漲300-400%'
                WHEN ((year_high - year_open) / year_open) * 100 < 500 THEN '05. 上漲400-500%'
                WHEN ((year_high - year_open) / year_open) * 100 < 600 THEN '06. 上漲500-600%'
                WHEN ((year_high - year_open) / year_open) * 100 < 700 THEN '07. 上漲600-700%'
                WHEN ((year_high - year_open) / year_open) * 100 < 800 THEN '08. 上漲700-800%'
                WHEN ((year_high - year_open) / year_open) * 100 < 900 THEN '09. 上漲800-900%'
                WHEN ((year_high - year_open) / year_open) * 100 < 1000 THEN '10. 上漲900-1000%'
                ELSE '11. 上漲1000%以上'
            END AS return_bin,
            CASE 
                WHEN ((year_high - year_open) / year_open) * 100 < 100 THEN 1
                WHEN ((year_high - year_open) / year_open) * 100 < 200 THEN 2
                WHEN ((year_high - year_open) / year_open) * 100 < 300 THEN 3
                WHEN ((year_high - year_open) / year_open) * 100 < 400 THEN 4
                WHEN ((year_high - year_open) / year_open) * 100 < 500 THEN 5
                WHEN ((year_high - year_open) / year_open) * 100 < 600 THEN 6
                WHEN ((year_high - year_open) / year_open) * 100 < 700 THEN 7
                WHEN ((year_high - year_open) / year_open) * 100 < 800 THEN 8
                WHEN ((year_high - year_open) / year_open) * 100 < 900 THEN 9
                WHEN ((year_high - year_open) / year_open) * 100 < 1000 THEN 10
                ELSE 11
            END AS bin_order
        FROM stock_annual_k
        WHERE year = '{year}'
        AND year_open IS NOT NULL AND year_high IS NOT NULL
        AND year_open > 0
    ),
    monthly_stats AS (
        SELECT stock_id, report_month, {metric_col} 
        FROM monthly_revenue
        WHERE report_month = '{prev_minguo_year}_12'
           OR (report_month LIKE '{minguo_year}_%' 
               AND report_month < '{minguo_year}_12'
               AND LENGTH(report_month) <= 7)
    )
    
    SELECT 
        b.return_bin,
        b.bin_order,
        COUNT(DISTINCT b.symbol) as stock_count,
        AVG(b.annual_max_return) as avg_max_return,  -- 該區間的平均最大漲幅
        ROUND(AVG(m.{metric_col})::numeric, 2) as mean_val,
        ROUND(percentile_cont(0.5) WITHIN GROUP (ORDER BY m.{metric_col})::numeric, 2) as median_val,
        ROUND(STDDEV(m.{metric_col})::numeric, 2) as std_val,
        ROUND(MIN(m.{metric_col})::numeric, 2) as min_val,
        ROUND(MAX(m.{metric_col})::numeric, 2) as max_val,
        ROUND((STDDEV(m.{metric_col}) / NULLIF(AVG(m.{metric_col}), 0))::numeric, 2) as cv_val,
        ROUND((percentile_cont(0.75) WITHIN GROUP (ORDER BY m.{metric_col}) - 
               percentile_cont(0.25) WITHIN GROUP (ORDER BY m.{metric_col}))::numeric, 2) as iqr_val,
        ROUND(SUM(CASE WHEN m.{metric_col} > 0 THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 1) as positive_rate
    FROM annual_bins b
    JOIN monthly_stats m ON SPLIT_PART(b.symbol, '.', 1) = m.stock_id
    WHERE m.{metric_col} IS NOT NULL
    GROUP BY b.return_bin, b.bin_order
    ORDER BY b.bin_order;
    """
    
    with engine.connect() as conn:
        return pd.read_sql_query(text(query), conn)

# ========== 6. 主頁面 ==========
st.title("🚀 StockRevenueLab: 最高價極限版")
st.markdown("#### 透過「年度最高價」計算，揭示股價可能達到的最大潛力漲幅")

# 重要提醒
with st.container():
    st.warning("""
    ⚠️ **重要提醒：最高價版本的特殊性**
    
    1. **計算方式不同**：使用「年度最高價」計算潛在最大漲幅
    2. **沒有下跌區間**：因為最高價一定 ≥ 開盤價，所以都是上漲區間
    3. **代表意義**：顯示「如果賣在年度最高點」的潛在報酬
    4. **實務應用**：適合設定目標價位，但不代表實際可實現報酬
    5. **波動更大**：數值通常比收盤價版本更高更極端
    """)

# 獲取數據
df = fetch_heatmap_data_high(target_year, target_col, stat_method)
stat_summary = fetch_stat_summary_high(target_year, target_col)

if not df.empty:
    # 頂部指標
    actual_months = df['report_month'].nunique()
    total_samples = df.groupby('return_bin')['stock_count'].max().sum()
    total_data_points = df['data_points'].sum() if 'data_points' in df.columns else 0
    
    c1, c2, c3, c4 = st.columns(4)
    with c1: st.metric("研究樣本總數", f"{int(total_samples):,} 檔")
    with c2: st.metric("分析年度", f"{target_year} 年")
    with c3: st.metric("數據月份數", f"{actual_months} 個月")
    with c4: st.metric("數據點總數", f"{int(total_data_points):,}")
    
    # 熱力圖
    st.subheader(f"📊 {target_year} 「最高價漲幅區間 vs {metric_choice}」業績對照熱力圖")
    st.info(f"**當前統計模式：{stat_method}** | 顏色深淺代表統計值的大小")
    
    pivot_df = df.pivot(index='return_bin', columns='report_month', values='val')
    
    # 根據統計方法選擇顏色方案
    if "標準差" in stat_method or "變異係數" in stat_method or "四分位距" in stat_method:
        color_scale = "Blues"
    elif "偏度" in stat_method:
        color_scale = "RdBu"
    elif "峰度" in stat_method:
        color_scale = "Viridis"
    elif "正樣本比例" in stat_method:
        color_scale = "Greens"
    else:
        color_scale = "RdYlGn"
    
    fig = px.imshow(
        pivot_df,
        labels=dict(x="報表月份", y="最高價漲幅區間", color=f"{metric_choice} ({df['stat_label'].iloc[0]})"),
        x=pivot_df.columns,
        y=pivot_df.index,
        color_continuous_scale=color_scale,
        aspect="auto",
        text_auto=".2f" if "變異係數" in stat_method or "峰度" in stat_method or "偏度" in stat_method else ".1f"
    )
    fig.update_xaxes(side="top")
    fig.update_layout(height=500)
    st.plotly_chart(fig, use_container_width=True)
    
    # 統計摘要
    with st.expander("📋 查看各漲幅區間詳細統計摘要", expanded=False):
        if not stat_summary.empty:
            stat_summary_display = stat_summary.rename(columns={
                'return_bin': '漲幅區間',
                'stock_count': '股票數量',
                'avg_max_return': '平均最大漲幅%',
                'mean_val': '平均值',
                'median_val': '中位數',
                'std_val': '標準差',
                'min_val': '最小值',
                'max_val': '最大值',
                'cv_val': '變異係數',
                'iqr_val': '四分位距',
                'positive_rate': '正增長比例%'
            })
            
            st.dataframe(
                stat_summary_display.style.format({
                    '平均最大漲幅%': '{:.1f}',
                    '平均值': '{:.1f}',
                    '中位數': '{:.1f}',
                    '標準差': '{:.1f}',
                    '最小值': '{:.1f}',
                    '最大值': '{:.1f}',
                    '變異係數': '{:.2f}',
                    '四分位距': '{:.1f}',
                    '正增長比例%': '{:.1f}%'
                }).background_gradient(cmap='YlOrRd', subset=['平均最大漲幅%', '平均值', '中位數'])
                .background_gradient(cmap='Blues', subset=['標準差', '四分位距'])
                .background_gradient(cmap='RdYlGn_r', subset=['變異係數'])
                .background_gradient(cmap='Greens', subset=['正增長比例%']),
                use_container_width=True,
                height=400
            )
    
    # AI分析
    with st.expander("🤖 AI智能分析助手", expanded=False):
        st.info("""
        💡 **使用說明**：
        複製下方完整分析指令，貼到AI對話框（如ChatGPT、Claude、DeepSeek）即可開始深度分析。
        """)
        
        # 簡單生成提示詞
        prompt_text = f"""
# 台股營收與股價最大潛力漲幅分析報告（最高價版本）

## 分析設定
- **分析年度**: {target_year}年
- **指標類型**: {metric_choice}
- **統計模式**: {stat_method}
- **樣本規模**: {total_samples:,}檔股票
- **數據特性**: 使用「年度最高價」計算潛在最大漲幅

## 重要提醒（請AI注意）
1. **這是「最高價版本」**：使用年度最高價(year_high)計算，代表「如果賣在年度最高點」的潛在報酬
2. **沒有下跌區間**：因為最高價一定≥開盤價，所有股票都歸類在上漲區間
3. **樂觀情境**：顯示股價可能達到的理論最大值
4. **波動更大**：數值通常比收盤價版本更高、更極端

## 統計摘要
{stat_summary.to_markdown() if not stat_summary.empty else "無統計數據"}

## 分析任務
請擔任專業量化分析師，分析以下問題：

### 1. 最大潛力分析
- 根據最高價數據，不同營收表現的股票「最大可能漲幅」分佈如何？
- 哪些營收特徵的股票有機會衝到500%以上的極限漲幅？

### 2. 目標價設定參考
- 投資人應如何參考這些「最高價數據」來設定合理的目標價位？
- 各漲幅區間的營收表現有何差異？

### 3. 風險考量
- 雖然最高價顯示潛力，但實際操作需要注意什麼風險？
- 如何平衡「追求最高價」與「實際可實現報酬」？

### 4. 策略建議
- 對於追求「極限報酬」的激進型投資者，有什麼具體策略建議？
- 如何搭配其他指標（如營收波動、市場情緒）來提高賣在相對高點的機率？

請用中文回答，結構清晰，並提供具體的數據支持。
"""
        
        st.code(prompt_text, language="text", height=300)
        
        col1, col2, col3 = st.columns(3)
        with col1:
            st.link_button("🔥 ChatGPT 分析", "https://chatgpt.com/")
        with col2:
            st.link_button("🔍 Claude 分析", "https://claude.ai/new")
        with col3:
            st.link_button("🚀 DeepSeek 分析", "https://chat.deepseek.com/")
    
else:
    st.warning(f"⚠️ 找不到 {target_year} 年的最高價數據。")

# ========== 7. 頁尾 ==========
st.markdown("---")
current_date = datetime.now()

col1, col2, col3 = st.columns(3)
with col1:
    st.markdown("**版本**：最高價極限版 1.0")
with col2:
    st.markdown("**計算方式**：年度最高價(year_high)")
with col3:
    st.markdown(f"**更新時間**：{current_date.strftime('%Y-%m-%d %H:%M')}")

st.caption("""
Developed by StockRevenueLab | 最高價極限版 | 揭示股價最大潛力 | 注意：此為樂觀情境分析
""")
