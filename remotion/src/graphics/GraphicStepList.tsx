/**
 * Numbered step list graphic (spec v7 §13.2). 2-4 steps, each a pill/row with a
 * number badge, revealed sequentially. Subtle motion, not bouncy.
 */
import React from 'react';
import {graphicTheme} from './graphic-theme';
import {GraphicStepListPayload} from './graphic-payloads';
import {GraphicFrame, useReveal} from './GraphicFrame';

const {colors, font, fontSize, radius} = graphicTheme;

const StepRow: React.FC<{label: string; text: string; delaySec: number}> = ({label, text, delaySec}) => {
  const reveal = useReveal(delaySec);
  return (
    <div
      style={{
        ...reveal,
        display: 'flex',
        alignItems: 'center',
        gap: 30,
        width: '100%',
        padding: '20px 30px',
        borderRadius: radius.panel,
        background: colors.paper,
        boxShadow: `0 8px 24px ${colors.shadow}`,
      }}
    >
      <div
        style={{
          flexShrink: 0,
          width: 72,
          height: 72,
          borderRadius: radius.pill,
          background: colors.warmOrange,
          color: colors.paper,
          fontFamily: font.family,
          fontSize: 42,
          fontWeight: 800,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
        }}
      >
        {label}
      </div>
      <span
        style={{
          fontFamily: font.family,
          fontSize: fontSize.item,
          fontWeight: 700,
          color: colors.text,
          lineHeight: 1.12,
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
  const steps = payload.steps.slice(0, 4);
  return (
    <GraphicFrame title={payload.title} footer={payload.footer}>
      <div style={{display: 'flex', flexDirection: 'column', gap: 26, width: '100%'}}>
        {steps.map((step, i) => (
          <StepRow key={i} label={step.label} text={step.text} delaySec={0.4 + i * 0.45} />
        ))}
      </div>
    </GraphicFrame>
  );
};
