/**
 * Time-block routine — calm editorial timeline (taste refresh C5).
 *
 * No time pills or block cards: each row is an accent time on the left and the
 * action text on the right, sharing a thin guide line so the time split scans
 * instantly as a vertical rhythm. Action text stays dominant.
 */
import React from 'react';
import {getGraphicColors, graphicTheme, type GraphicColorRoles} from './graphic-theme';
import {GraphicRoutineSplitPayload} from './graphic-payloads';
import {GraphicFrame, useReveal} from './GraphicFrame';

const {font, fontSize, motion} = graphicTheme;

const RoutineBlock: React.FC<{
  time: string;
  text: string;
  colors: GraphicColorRoles;
  delaySec: number;
}> = ({time, text, colors, delaySec}) => {
  const reveal = useReveal(delaySec);
  return (
    <div
      style={{
        ...reveal,
        display: 'grid',
        gridTemplateColumns: '150px 1fr',
        alignItems: 'baseline',
        gap: 26,
        width: '100%',
      }}
    >
      {/* Right-aligned accent time — the column lines up so 10 / 10 / 10 reads
          as a split at a glance, but stays secondary to the action. */}
      <span
        style={{
          textAlign: 'right',
          fontFamily: font.family,
          fontSize: 40,
          fontWeight: 700,
          color: colors.accent,
          lineHeight: 1.05,
          fontVariantNumeric: 'tabular-nums',
        }}
      >
        {time}
      </span>
      <span
        style={{
          paddingLeft: 26,
          borderLeft: `3px solid ${colors.accentSoft}`,
          fontFamily: font.family,
          fontSize: fontSize.item,
          fontWeight: 600,
          color: colors.textPrimary,
          lineHeight: 1.16,
          display: '-webkit-box',
          WebkitLineClamp: 2,
          WebkitBoxOrient: 'vertical',
          overflow: 'hidden',
        }}
      >
        {text}
      </span>
    </div>
  );
};

const TotalEyebrow: React.FC<{children: React.ReactNode; colors: GraphicColorRoles}> = ({children, colors}) => {
  const reveal = useReveal(motion.itemStartSec - 0.08);
  return (
    <div
      style={{
        ...reveal,
        alignSelf: 'center',
        fontFamily: font.family,
        fontSize: 30,
        fontWeight: 700,
        letterSpacing: 2,
        textTransform: 'uppercase',
        color: colors.textMuted,
        lineHeight: 1,
      }}
    >
      {children}
    </div>
  );
};

export const RoutineSplitGraphic: React.FC<{
  payload: GraphicRoutineSplitPayload;
  durationInFrames: number;
  fps: number;
}> = ({payload}) => {
  const colors = getGraphicColors(payload.variant);
  const blocks = payload.blocks.slice(0, 4);
  return (
    <GraphicFrame
      title={payload.title}
      footer={payload.footer}
      variant={payload.variant}
      surfaceStyle={payload.surface_style}
    >
      <div
        style={{
          width: '100%',
          maxWidth: 860,
          margin: '0 auto',
          display: 'flex',
          flexDirection: 'column',
          gap: 40,
        }}
      >
        {payload.totalLabel && <TotalEyebrow colors={colors}>{payload.totalLabel}</TotalEyebrow>}
        {blocks.map((block, i) => (
          <RoutineBlock
            key={`${block.time}-${i}`}
            time={block.time}
            text={block.text}
            colors={colors}
            delaySec={motion.itemStartSec + i * motion.itemStaggerSec}
          />
        ))}
      </div>
    </GraphicFrame>
  );
};
