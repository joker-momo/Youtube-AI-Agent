/**
 * Two-choice comparison — calm editorial two columns (taste refresh C6).
 *
 * No card boxes or pills: two open columns separated by one subtle vertical
 * divider, each led by an accent heading with dominant body text. Framed as a
 * helpful choice, never fear. One clear opposition, readable in ~1s.
 */
import React from 'react';
import {getGraphicColors, graphicTheme, type GraphicColorRoles} from './graphic-theme';
import {GraphicComparisonPayload} from './graphic-payloads';
import {GraphicFrame, useReveal} from './GraphicFrame';

const {font, fontSize, motion} = graphicTheme;

const ChoiceColumn: React.FC<{
  heading: string;
  text: string;
  badge?: string;
  tone: 'positive' | 'caution';
  colors: GraphicColorRoles;
  delaySec: number;
}> = ({heading, text, badge, tone, colors, delaySec}) => {
  const reveal = useReveal(delaySec);
  const accent = tone === 'positive' ? colors.positive : colors.caution;
  return (
    <div
      style={{
        ...reveal,
        flex: 1,
        minWidth: 0,
        display: 'flex',
        flexDirection: 'column',
        gap: 16,
        padding: '0 8px',
      }}
    >
      {/* Accent heading anchors the choice (MEJOR / CUIDADO). */}
      <div
        style={{
          fontFamily: font.family,
          fontSize: 34,
          fontWeight: 800,
          letterSpacing: 1.5,
          color: accent,
          lineHeight: 1.05,
        }}
      >
        {heading}
      </div>
      {/* Body is the dominant readable element. */}
      <div
        style={{
          fontFamily: font.family,
          fontSize: fontSize.item,
          fontWeight: 600,
          color: colors.textPrimary,
          lineHeight: 1.16,
          display: '-webkit-box',
          WebkitLineClamp: 3,
          WebkitBoxOrient: 'vertical',
          overflow: 'hidden',
        }}
      >
        {text}
      </div>
      {badge && (
        <div
          style={{
            fontFamily: font.family,
            fontSize: 28,
            fontWeight: 600,
            color: colors.textMuted,
            lineHeight: 1.2,
          }}
        >
          {badge}
        </div>
      )}
    </div>
  );
};

export const ComparisonGraphic: React.FC<{
  payload: GraphicComparisonPayload;
  durationInFrames: number;
  fps: number;
}> = ({payload}) => {
  const colors = getGraphicColors(payload.variant);
  return (
    <GraphicFrame
      title={payload.title}
      footer={payload.footer}
      variant={payload.variant}
      surfaceStyle={payload.surface_style}
    >
      <div style={{display: 'flex', alignItems: 'stretch', width: '100%', maxWidth: 940, margin: '0 auto'}}>
        <ChoiceColumn {...payload.left} tone="positive" colors={colors} delaySec={motion.itemStartSec} />
        {/* One subtle vertical divider — thin and low opacity, not a heavy rule. */}
        <div style={{width: 1, alignSelf: 'stretch', background: colors.lineStrong, opacity: 0.6, margin: '6px 26px'}} />
        <ChoiceColumn {...payload.right} tone="caution" colors={colors} delaySec={motion.itemStartSec + motion.itemStaggerSec} />
      </div>
    </GraphicFrame>
  );
};
