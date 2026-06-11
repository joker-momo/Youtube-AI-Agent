/**
 * Label-reading graphic — clean editorial nutrition label (taste refresh C7).
 *
 * Keeps a strong solid product bar so it still reads as "inspecting a label",
 * but the callouts become typographic rows (label left, prominent value right,
 * muted note) separated by thin dividers — no per-row cards, pills or shadows.
 */
import React from 'react';
import {getGraphicColors, graphicTheme, type GraphicColorRoles} from './graphic-theme';
import {GraphicLabelCalloutPayload} from './graphic-payloads';
import {GraphicFrame, useReveal} from './GraphicFrame';

const {font, fontSize, radius, motion} = graphicTheme;

const CalloutRow: React.FC<{
  label: string;
  value: string;
  note?: string;
  colors: GraphicColorRoles;
  delaySec: number;
  withDivider: boolean;
}> = ({label, value, note, colors, delaySec, withDivider}) => {
  const reveal = useReveal(delaySec);
  return (
    <div
      style={{
        ...reveal,
        display: 'grid',
        gridTemplateColumns: '1fr auto',
        gap: 24,
        alignItems: 'baseline',
        padding: '22px 8px',
        borderTop: withDivider ? `1px solid ${colors.lineStrong}` : 'none',
      }}
    >
      <div style={{minWidth: 0}}>
        <div
          style={{
            fontFamily: font.family,
            fontSize: fontSize.small,
            fontWeight: 700,
            color: colors.titleStrong,
            lineHeight: 1.08,
          }}
        >
          {label}
        </div>
        {note && (
          <div
            style={{
              marginTop: 6,
              fontFamily: font.family,
              fontSize: 26,
              fontWeight: 600,
              color: colors.textMuted,
              lineHeight: 1.18,
            }}
          >
            {note}
          </div>
        )}
      </div>
      {/* Value is the standout after the title. */}
      <div
        style={{
          fontFamily: font.family,
          fontSize: 52,
          fontWeight: 800,
          color: colors.textPrimary,
          lineHeight: 1,
          whiteSpace: 'nowrap',
          fontVariantNumeric: 'tabular-nums',
        }}
      >
        {value}
      </div>
    </div>
  );
};

const ProductBar: React.FC<{children: React.ReactNode; colors: GraphicColorRoles}> = ({children, colors}) => {
  const reveal = useReveal(motion.itemStartSec - 0.12);
  return (
    <div
      style={{
        ...reveal,
        alignSelf: 'stretch',
        padding: '16px 24px',
        borderRadius: radius.panel,
        background: colors.positive,
        color: colors.textInverse,
        fontFamily: font.family,
        fontSize: 38,
        fontWeight: 800,
        letterSpacing: 0.5,
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
          maxWidth: 860,
          margin: '0 auto',
          display: 'flex',
          flexDirection: 'column',
          gap: 20,
        }}
      >
        {payload.productLabel && <ProductBar colors={colors}>{payload.productLabel}</ProductBar>}
        <div style={{display: 'flex', flexDirection: 'column'}}>
          {callouts.map((callout, i) => (
            <CalloutRow
              key={`${callout.label}-${i}`}
              label={callout.label}
              value={callout.value}
              note={callout.note}
              colors={colors}
              delaySec={motion.itemStartSec + i * motion.itemStaggerSec}
              withDivider={i > 0}
            />
          ))}
        </div>
      </div>
    </GraphicFrame>
  );
};
