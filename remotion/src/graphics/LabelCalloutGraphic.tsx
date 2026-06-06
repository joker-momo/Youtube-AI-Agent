/**
 * Label-reading graphic for supermarket/nutrition moments.
 * Uses a generic label card with highlighted callout rows, never a real brand.
 */
import React from 'react';
import {getGraphicColors, graphicTheme, type GraphicColorRoles} from './graphic-theme';
import {GraphicLabelCalloutPayload} from './graphic-payloads';
import {GraphicFrame, useReveal} from './GraphicFrame';

const {font, fontSize, radius} = graphicTheme;

const CalloutRow: React.FC<{
  label: string;
  value: string;
  note?: string;
  colors: GraphicColorRoles;
  delaySec: number;
}> = ({label, value, note, colors, delaySec}) => {
  const reveal = useReveal(delaySec);
  return (
    <div
      style={{
        ...reveal,
        display: 'grid',
        gridTemplateColumns: '1fr auto',
        gap: 18,
        alignItems: 'center',
        padding: '18px 24px',
        borderRadius: radius.panel,
        background: colors.paper,
        boxShadow: `0 8px 24px ${colors.shadow}`,
        border: `2px solid ${colors.line}`,
        width: '100%',
      }}
    >
      <div style={{minWidth: 0}}>
        <div
          style={{
            fontFamily: font.family,
            fontSize: fontSize.small,
            fontWeight: 800,
            color: colors.oliveDark,
            lineHeight: 1.05,
          }}
        >
          {label}
        </div>
        {note && (
          <div
            style={{
              marginTop: 6,
              fontFamily: font.family,
              fontSize: 28,
              fontWeight: 600,
              color: colors.mutedText,
              lineHeight: 1.15,
            }}
          >
            {note}
          </div>
        )}
      </div>
      <div
        style={{
          fontFamily: font.family,
          fontSize: 42,
          fontWeight: 800,
          color: colors.text,
          lineHeight: 1,
          whiteSpace: 'nowrap',
        }}
      >
        {value}
      </div>
    </div>
  );
};

const ProductLabel: React.FC<{children: React.ReactNode; colors: GraphicColorRoles}> = ({children, colors}) => {
  const reveal = useReveal(0.2);
  return (
    <div
      style={{
        ...reveal,
        alignSelf: 'stretch',
        padding: '16px 24px',
        borderRadius: radius.panel,
        background: colors.olive,
        color: colors.paper,
        fontFamily: font.family,
        fontSize: 38,
        fontWeight: 800,
        textAlign: 'center',
        lineHeight: 1.05,
      }}
    >
      {children}
    </div>
  );
};

export const LabelCalloutGraphic: React.FC<{
  payload: GraphicLabelCalloutPayload;
  durationInFrames: number;
  fps: number;
}> = ({payload}) => {
  const colors = getGraphicColors(payload.variant);
  const callouts = payload.callouts.slice(0, 4);
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
          maxWidth: 880,
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          gap: 18,
        }}
      >
        {payload.productLabel && (
          <ProductLabel colors={colors}>{payload.productLabel}</ProductLabel>
        )}
        {callouts.map((callout, i) => (
          <CalloutRow
            key={`${callout.label}-${i}`}
            label={callout.label}
            value={callout.value}
            note={callout.note}
            colors={colors}
            delaySec={0.45 + i * 0.35}
          />
        ))}
      </div>
    </GraphicFrame>
  );
};
