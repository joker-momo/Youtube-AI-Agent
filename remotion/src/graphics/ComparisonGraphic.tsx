/**
 * Two-choice comparison graphic. Framed as helpful choice-making, not fear.
 */
import React from 'react';
import {graphicTheme} from './graphic-theme';
import {GraphicComparisonPayload} from './graphic-payloads';
import {GraphicFrame, useReveal} from './GraphicFrame';

const {colors, font, fontSize, radius} = graphicTheme;

const ChoicePanel: React.FC<{
  heading: string;
  text: string;
  badge?: string;
  tone: 'positive' | 'caution';
  delaySec: number;
}> = ({heading, text, badge, tone, delaySec}) => {
  const reveal = useReveal(delaySec);
  const accent = tone === 'positive' ? colors.olive : colors.warmOrange;
  return (
    <div
      style={{
        ...reveal,
        flex: 1,
        minWidth: 0,
        padding: '30px 28px',
        borderRadius: radius.panel,
        background: colors.paper,
        boxShadow: `0 10px 28px ${colors.shadow}`,
        borderTop: `12px solid ${accent}`,
        display: 'flex',
        flexDirection: 'column',
        gap: 18,
      }}
    >
      <div
        style={{
          fontFamily: font.family,
          fontSize: 40,
          fontWeight: 800,
          color: accent,
          lineHeight: 1.05,
        }}
      >
        {heading}
      </div>
      <div
        style={{
          fontFamily: font.family,
          fontSize: fontSize.item,
          fontWeight: 800,
          color: colors.text,
          lineHeight: 1.1,
        }}
      >
        {text}
      </div>
      {badge && (
        <div
          style={{
            alignSelf: 'flex-start',
            padding: '10px 18px',
            borderRadius: radius.pill,
            background: tone === 'positive' ? 'rgba(124,138,74,0.14)' : 'rgba(217,154,78,0.16)',
            color: colors.mutedText,
            fontFamily: font.family,
            fontSize: 30,
            fontWeight: 700,
            lineHeight: 1,
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
  return (
    <GraphicFrame title={payload.title} footer={payload.footer}>
      <div style={{display: 'flex', gap: 24, width: '100%', maxWidth: 920}}>
        <ChoicePanel {...payload.left} tone="positive" delaySec={0.35} />
        <ChoicePanel {...payload.right} tone="caution" delaySec={0.65} />
      </div>
    </GraphicFrame>
  );
};
