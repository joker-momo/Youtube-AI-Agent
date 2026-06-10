/**
 * Numbered step list — calm typographic steps (taste refresh C4).
 *
 * No orange number pills or card rows: each step is a large ghosted numeral
 * (a quiet ordering anchor) beside the step text, which stays the dominant
 * readable element. Quick gentle cascade, no badges.
 */
import React from 'react';
import {getGraphicColors, graphicTheme, type GraphicColorRoles} from './graphic-theme';
import {GraphicStepListPayload} from './graphic-payloads';
import {GraphicFrame, useReveal} from './GraphicFrame';

const {font, fontSize, motion} = graphicTheme;

const StepRow: React.FC<{
  label: string;
  text: string;
  colors: GraphicColorRoles;
  delaySec: number;
}> = ({label, text, colors, delaySec}) => {
  const reveal = useReveal(delaySec);
  return (
    <div style={{...reveal, display: 'flex', alignItems: 'baseline', gap: 26, width: '100%'}}>
      {/* Ghosted numeral: big enough to scan order, faint enough to never
          out-shout the step text. */}
      <span
        style={{
          flexShrink: 0,
          width: 78,
          textAlign: 'right',
          fontFamily: font.family,
          fontSize: 76,
          fontWeight: 800,
          lineHeight: 0.9,
          color: colors.accent,
          opacity: 0.32,
          fontVariantNumeric: 'tabular-nums',
        }}
      >
        {label}
      </span>
      <span
        style={{
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

export const GraphicStepList: React.FC<{
  payload: GraphicStepListPayload;
  durationInFrames: number;
  fps: number;
}> = ({payload}) => {
  const colors = getGraphicColors(payload.variant);
  const steps = payload.steps.slice(0, 4);
  return (
    <GraphicFrame
      title={payload.title}
      footer={payload.footer}
      variant={payload.variant}
      surfaceStyle={payload.surface_style}
    >
      <div
        style={{
          display: 'flex',
          flexDirection: 'column',
          gap: 42,
          width: '100%',
          maxWidth: 860,
          margin: '0 auto',
        }}
      >
        {steps.map((step, i) => (
          <StepRow
            key={i}
            label={step.label}
            text={step.text}
            colors={colors}
            delaySec={motion.itemStartSec + i * motion.itemStaggerSec}
          />
        ))}
      </div>
    </GraphicFrame>
  );
};
