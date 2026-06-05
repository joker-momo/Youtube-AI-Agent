/**
 * Checklist graphic (spec v7 §13.1). 2-5 items, one revealed at a time, calm
 * warm checkmarks. Large text, no full-screen card feel.
 */
import React from 'react';
import {graphicTheme} from './graphic-theme';
import {GraphicChecklistPayload} from './graphic-payloads';
import {GraphicFrame, useReveal} from './GraphicFrame';

const {colors, font, fontSize} = graphicTheme;

const Check: React.FC = () => (
  <svg width={56} height={56} viewBox="0 0 56 56" style={{flexShrink: 0}}>
    <circle cx={28} cy={28} r={26} fill={colors.olive} />
    <path
      d="M16 29 L24 37 L40 19"
      fill="none"
      stroke={colors.paper}
      strokeWidth={5}
      strokeLinecap="round"
      strokeLinejoin="round"
    />
  </svg>
);

const ChecklistItem: React.FC<{children: React.ReactNode; delaySec: number}> = ({children, delaySec}) => {
  const reveal = useReveal(delaySec);
  return (
    <div
      style={{
        ...reveal,
        display: 'flex',
        alignItems: 'center',
        gap: 28,
        width: '100%',
        padding: '18px 28px',
        borderRadius: graphicTheme.radius.panel,
        background: colors.paper,
        boxShadow: `0 8px 24px ${colors.shadow}`,
      }}
    >
      <Check />
      <span
        style={{
          fontFamily: font.family,
          fontSize: fontSize.item,
          fontWeight: 700,
          color: colors.text,
          lineHeight: 1.1,
        }}
      >
        {children}
      </span>
    </div>
  );
};

export const GraphicChecklist: React.FC<{
  payload: GraphicChecklistPayload;
  durationInFrames: number;
  fps: number;
}> = ({payload}) => {
  const items = payload.items.slice(0, 5);
  return (
    <GraphicFrame title={payload.title} footer={payload.footer}>
      <div style={{display: 'flex', flexDirection: 'column', gap: 26, width: '100%'}}>
        {items.map((item, i) => (
          <ChecklistItem key={i} delaySec={0.4 + i * 0.45}>
            {item}
          </ChecklistItem>
        ))}
      </div>
    </GraphicFrame>
  );
};
