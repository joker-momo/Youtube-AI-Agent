/**
 * Time-block routine graphic for practical routines such as 10 + 10 + 10 min.
 */
import React from 'react';
import {getGraphicColors, graphicTheme, type GraphicColorRoles} from './graphic-theme';
import {GraphicRoutineSplitPayload} from './graphic-payloads';
import {GraphicFrame, useReveal} from './GraphicFrame';

const {font, fontSize, radius} = graphicTheme;

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
        gridTemplateColumns: '160px 1fr',
        alignItems: 'center',
        gap: 26,
        width: '100%',
        padding: '22px 28px',
        borderRadius: radius.panel,
        background: colors.paper,
        boxShadow: `0 8px 24px ${colors.shadow}`,
      }}
    >
      <div
        style={{
          borderRadius: radius.pill,
          background: colors.warmOrange,
          color: colors.paper,
          fontFamily: font.family,
          fontSize: 38,
          fontWeight: 800,
          textAlign: 'center',
          padding: '16px 12px',
          lineHeight: 1,
        }}
      >
        {time}
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
    </div>
  );
};

const TotalLabel: React.FC<{children: React.ReactNode; colors: GraphicColorRoles}> = ({children, colors}) => {
  const reveal = useReveal(0.2);
  return (
    <div
      style={{
        ...reveal,
        alignSelf: 'center',
        padding: '12px 30px',
        borderRadius: radius.pill,
        background: colors.positiveSoft,
        color: colors.oliveDark,
        fontFamily: font.family,
        fontSize: 40,
        fontWeight: 800,
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
          maxWidth: 880,
          display: 'flex',
          flexDirection: 'column',
          gap: 22,
          alignItems: 'stretch',
        }}
      >
        {payload.totalLabel && (
          <TotalLabel colors={colors}>{payload.totalLabel}</TotalLabel>
        )}
        {blocks.map((block, i) => (
          <RoutineBlock
            key={`${block.time}-${i}`}
            time={block.time}
            text={block.text}
            colors={colors}
            delaySec={0.45 + i * 0.35}
          />
        ))}
      </div>
    </GraphicFrame>
  );
};
