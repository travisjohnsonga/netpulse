import ReactECharts from 'echarts-for-react'
import type { EChartsOption } from 'echarts'

interface Props {
  value: number
  label: string
  /** When true, higher is worse (risk). Colors invert. Default false. */
  invert?: boolean
  height?: number
}

// Green→yellow→orange→red bands. For score gauges (higher = better) we flip the
// gradient so high scores read green; for risk (higher = worse) red is high.
export default function Gauge({ value, label, invert = false, height = 180 }: Props) {
  const v = Math.max(0, Math.min(100, value))
  const bands: [number, string][] = invert
    ? [[0.6, '#22c55e'], [0.8, '#eab308'], [0.9, '#f97316'], [1, '#ef4444']]
    : [[0.4, '#ef4444'], [0.6, '#f97316'], [0.8, '#eab308'], [1, '#22c55e']]

  const option: EChartsOption = {
    series: [
      {
        type: 'gauge',
        startAngle: 210,
        endAngle: -30,
        min: 0,
        max: 100,
        progress: { show: false },
        axisLine: { lineStyle: { width: 14, color: bands } },
        pointer: { width: 4, length: '60%', itemStyle: { color: 'auto' } },
        axisTick: { show: false },
        splitLine: { length: 10, lineStyle: { color: '#fff', width: 2 } },
        axisLabel: { show: false },
        anchor: { show: false },
        title: { offsetCenter: [0, '58%'], fontSize: 12, color: '#6b7280' },
        detail: {
          valueAnimation: true,
          offsetCenter: [0, '14%'],
          fontSize: 26,
          fontWeight: 'bold',
          color: '#111827',
          formatter: '{value}',
        },
        data: [{ value: Math.round(v), name: label }],
      },
    ],
  }
  return <ReactECharts option={option} style={{ height }} opts={{ renderer: 'svg' }} />
}
