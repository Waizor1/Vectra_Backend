<template>
	<div class="chart">
		<div v-if="title || subtitle" class="chart__head">
			<div v-if="title" class="chart__title">{{ title }}</div>
			<div v-if="subtitle" class="chart__subtitle">{{ subtitle }}</div>
		</div>
		<div ref="chartEl" class="chart__canvas" :style="{ height: heightStyle }" />
	</div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import * as echarts from 'echarts/core';
import { LineChart } from 'echarts/charts';
import { GridComponent, TooltipComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';

echarts.use([LineChart, GridComponent, TooltipComponent, CanvasRenderer]);

const props = defineProps({
	title: {
		type: String,
		default: '',
	},
	subtitle: {
		type: String,
		default: '',
	},
	height: {
		type: [Number, String],
		default: 320,
	},
	categories: {
		type: Array,
		default: () => [],
	},
	series: {
		type: Array,
		default: () => [],
	},
	valueFormatter: {
		type: Function,
		default: (v) => String(v ?? '—'),
	},
	mode: {
		type: String,
		default: '30d',
	},
});

const chartEl = ref(null);
let chart = null;
let resizeObserver = null;

const heightStyle = computed(() => (typeof props.height === 'number' ? `${props.height}px` : String(props.height)));

function compactAxisValue(value) {
	const n = Number(value);
	if (!Number.isFinite(n)) return '—';
	if (Math.abs(n) >= 1000000) return `${(n / 1000000).toFixed(1)}M`;
	if (Math.abs(n) >= 1000) return `${(n / 1000).toFixed(1)}K`;
	return n.toLocaleString('ru-RU');
}

function normalizeSeries() {
	if (!Array.isArray(props.series)) return [];
	return props.series
		.map((item) => {
			const data = Array.isArray(item?.data) ? item.data.map((v) => (Number.isFinite(Number(v)) ? Number(v) : 0)) : [];
			return {
				name: String(item?.name || 'Метрика'),
				data,
				color: String(item?.color || '#44b6ff'),
			};
		})
		.filter((item) => item.data.length);
}

function optionForData() {
	const categories = Array.isArray(props.categories) ? props.categories.map((x) => String(x ?? '')) : [];
	const series = normalizeSeries();
	const hasData = series.some((s) => s.data.some((v) => Number(v) > 0));

	return {
		animationDuration: 520,
		animationEasing: 'cubicOut',
		grid: {
			left: 12,
			right: 14,
			top: 18,
			bottom: 22,
			containLabel: true,
		},
		tooltip: {
			trigger: 'axis',
			confine: true,
			borderWidth: 1,
			borderColor: 'rgba(255, 255, 255, 0.18)',
			backgroundColor: 'rgba(8, 16, 32, 0.94)',
			textStyle: {
				color: '#EAF2FF',
				fontSize: 12,
			},
			axisPointer: {
				type: 'line',
				lineStyle: {
					color: 'rgba(255, 255, 255, 0.20)',
				},
			},
			formatter(params) {
				if (!Array.isArray(params) || !params.length) return '—';
				const label = params[0]?.axisValueLabel ?? '';
				const rows = params
					.map((p) => {
						const marker = p.marker || '';
						const val = props.valueFormatter(Number(p.value));
						return `${marker} ${p.seriesName}: <b>${val}</b>`;
					})
					.join('<br/>');
				return `${label}<br/>${rows}`;
			},
		},
		xAxis: {
			type: 'category',
			boundaryGap: false,
			data: categories,
			axisLine: {
				lineStyle: {
					color: 'rgba(255, 255, 255, 0.14)',
				},
			},
			axisTick: { show: false },
			axisLabel: {
				color: 'rgba(215, 231, 255, 0.70)',
				fontSize: 11,
				interval: props.mode === '12m' ? 0 : 'auto',
				hideOverlap: true,
			},
		},
		yAxis: {
			type: 'value',
			axisLabel: {
				color: 'rgba(215, 231, 255, 0.68)',
				fontSize: 11,
				formatter: (v) => compactAxisValue(v),
			},
			splitLine: {
				lineStyle: {
					color: 'rgba(255, 255, 255, 0.07)',
				},
			},
		},
		graphic: hasData
			? undefined
			: [
					{
						type: 'text',
						left: 'center',
						top: 'center',
						style: {
							text: 'Нет данных для графика',
							fill: 'rgba(215, 231, 255, 0.56)',
							fontSize: 13,
							fontWeight: 500,
						},
					},
				],
		series: series.map((item) => ({
			name: item.name,
			type: 'line',
			smooth: true,
			showSymbol: false,
			symbolSize: 7,
			data: item.data,
			lineStyle: {
				width: 3,
				color: item.color,
			},
			emphasis: {
				scale: true,
				focus: 'series',
			},
			areaStyle: {
				opacity: 0.24,
				color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
					{ offset: 0, color: item.color },
					{ offset: 1, color: 'rgba(0, 0, 0, 0)' },
				]),
			},
		})),
	};
}

function renderChart() {
	if (!chart) return;
	chart.setOption(optionForData(), true);
}

function ensureChart() {
	if (!chartEl.value) return;
	if (!chart) {
		chart = echarts.init(chartEl.value, undefined, { renderer: 'canvas' });
	}
	renderChart();
}

onMounted(() => {
	ensureChart();
	if (typeof ResizeObserver !== 'undefined' && chartEl.value) {
		resizeObserver = new ResizeObserver(() => {
			if (chart) chart.resize();
		});
		resizeObserver.observe(chartEl.value);
	}
});

watch(
	() => [props.categories, props.series, props.mode, props.height],
	() => {
		ensureChart();
	},
	{ deep: true }
);

onBeforeUnmount(() => {
	if (resizeObserver) {
		resizeObserver.disconnect();
		resizeObserver = null;
	}
	if (chart) {
		chart.dispose();
		chart = null;
	}
});
</script>

<style scoped>
.chart {
	display: grid;
	gap: 8px;
}

.chart__head {
	display: grid;
	gap: 4px;
}

.chart__title {
	font-weight: 700;
	font-size: 15px;
	color: #f3f7ff;
}

.chart__subtitle {
	font-size: 12px;
	color: rgba(220, 235, 255, 0.72);
}

.chart__canvas {
	width: 100%;
	border-radius: 16px;
	background: radial-gradient(circle at 0 0, rgba(120, 174, 255, 0.14), rgba(6, 14, 28, 0.72) 56%), linear-gradient(180deg, rgba(7, 16, 32, 0.98), rgba(7, 14, 30, 0.88));
	border: 1px solid rgba(147, 188, 255, 0.22);
	box-shadow: inset 0 0 0 1px rgba(15, 28, 52, 0.45), 0 24px 60px rgba(0, 0, 0, 0.35);
}
</style>
