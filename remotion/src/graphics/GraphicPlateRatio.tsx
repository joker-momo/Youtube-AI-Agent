/**
 * Plate-ratio graphic (spec v7 §13.3). A circular plate split by segment value
 * (e.g. 50/25/25), wedges revealed in sequence, large readable labels. The
 * plate/shape is the focus — not a card of text, not a finance pie chart.
 */
import React from 'react';
import {interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';
import {getGraphicColors, graphicTheme, type GraphicColorRoles} from './graphic-theme';
import {GraphicPlateRatioPayload} from './graphic-payloads';
import {GraphicFrame, useReveal} from './GraphicFrame';

const {font, fontSize, radius} = graphicTheme;

const CX = 270;
const CY = 270;
const R = 250;

function polar(cx: number, cy: number, r: number, angleDeg: number): [number, number] {
  const a = ((angleDeg - 90) * Math.PI) / 180;
  return [cx + r * Math.cos(a), cy + r * Math.sin(a)];
}

function wedgePath(startDeg: number, endDeg: number): string {
  const [x0, y0] = polar(CX, CY, R, startDeg);
  const [x1, y1] = polar(CX, CY, R, endDeg);
  const largeArc = endDeg - startDeg > 180 ? 1 : 0;
  return `M ${CX} ${CY} L ${x0} ${y0} A ${R} ${R} 0 ${largeArc} 1 ${x1} ${y1} Z`;
}

const Wedge: React.FC<{
  d: string;
  fill: string;
  colors: GraphicColorRoles;
  delaySec: number;
}> = ({d, fill, colors, delaySec}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const progress = spring({
    frame: frame - Math.round(delaySec * fps),
    fps,
    config: {damping: 200, mass: 0.7},
    durationInFrames: Math.round(fps * 0.5),
  });
  const opacity = interpolate(progress, [0, 1], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  return <path d={d} fill={fill} opacity={opacity} stroke={colors.paper} strokeWidth={6} />;
};

const Chip: React.FC<{
  label: string;
  color: string;
  colors: GraphicColorRoles;
  delaySec: number;
}> = ({label, color, colors, delaySec}) => {
  const reveal = useReveal(delaySec);
  return (
    <div
      style={{
        ...reveal,
        display: 'flex',
        alignItems: 'center',
        gap: 18,
        padding: '14px 26px',
        borderRadius: radius.pill,
        background: colors.paper,
        boxShadow: `0 6px 18px ${colors.shadow}`,
      }}
    >
      <span style={{width: 28, height: 28, borderRadius: 999, background: color, flexShrink: 0}} />
      <span
        style={{
          fontFamily: font.family,
          fontSize: fontSize.small + 4,
          fontWeight: 700,
          color: colors.text,
          whiteSpace: 'nowrap',
        }}
      >
        {label}
      </span>
    </div>
  );
};

export const GraphicPlateRatio: React.FC<{
  payload: GraphicPlateRatioPayload;
  durationInFrames: number;
  fps: number;
}> = ({payload}) => {
  const colors = getGraphicColors(payload.variant);
  const segmentColors = [colors.vegetables, colors.protein, colors.carbs, colors.softTerracotta];
  const total = payload.segments.reduce((sum, s) => sum + s.value, 0) || 100;

  let cursor = 0;
  const wedges = payload.segments.map((seg, i) => {
    const start = (cursor / total) * 360;
    cursor += seg.value;
    const end = (cursor / total) * 360;
    return {
      d: wedgePath(start, end),
      color: segmentColors[i % segmentColors.length],
      label: seg.label,
      delaySec: 0.5 + i * 0.5,
    };
  });

  return (
    <GraphicFrame
      title={payload.title}
      footer={payload.footer}
      variant={payload.variant}
      surfaceStyle={payload.surface_style}
    >
      <div style={{display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 40}}>
        <svg width={540} height={540} viewBox="0 0 540 540">
          {/* Plate rim. */}
          <circle cx={CX} cy={CY} r={R + 14} fill={colors.paper} stroke={colors.line} strokeWidth={4} />
          {wedges.map((w, i) => (
            <Wedge key={i} d={w.d} fill={w.color} colors={colors} delaySec={w.delaySec} />
          ))}
        </svg>

        <div style={{display: 'flex', flexWrap: 'wrap', justifyContent: 'center', gap: 18, maxWidth: 900}}>
          {wedges.map((w, i) => (
            <Chip key={i} label={w.label} color={w.color} colors={colors} delaySec={w.delaySec + 0.15} />
          ))}
        </div>
      </div>
    </GraphicFrame>
  );
};
