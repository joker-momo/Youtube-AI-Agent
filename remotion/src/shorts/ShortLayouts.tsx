/**
 * Universal Short Video layout registry (spec §7 + §8).
 *
 * Each layout is a pure positional component. It receives the generic payload
 * the scene-builder LLM produced and renders it into the canonical y-zones
 * for that layout type. Background and overlays are rendered by the parent.
 */
import React from 'react';
import {AbsoluteFill} from 'remotion';
import {HookTitle, BodyText, CaptionText, BulletPill, CtaText} from './ShortText';
import {SHORT_LAYOUT, ShortLayoutName} from './ShortLayoutConstants';

export type ShortLayoutPayload = {
  title?: string;
  subtitle?: string;
  body?: string;
  bullets?: string[];
  emphasis?: string;
  cta?: string;
  cover_text?: string;
};

export type ShortSceneInput = {
  on_screen_text?: string;
  caption?: string;
  layout_payload?: ShortLayoutPayload;
  accentColor?: string;
};

/** Anchor a child inside a vertical y-band (yMin..yMax) on a 1080-wide canvas. */
const Zone: React.FC<{
  yMin: number;
  yMax: number;
  width: number;
  children: React.ReactNode;
  justify?: 'start' | 'center' | 'end';
}> = ({yMin, yMax, width, children, justify = 'center'}) => (
  <AbsoluteFill
    style={{
      top: yMin,
      height: yMax - yMin,
      left: (SHORT_LAYOUT.width - width) / 2,
      width,
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      justifyContent: justify === 'start' ? 'flex-start' : justify === 'end' ? 'flex-end' : 'center',
      gap: 24,
    }}
  >
    {children}
  </AbsoluteFill>
);

const textOrEmpty = (s?: string): string => (s && s.trim() ? s.trim() : '');

// 8.1 short_hook ------------------------------------------------------------
export const ShortHookLayout: React.FC<ShortSceneInput> = ({on_screen_text, layout_payload, accentColor}) => {
  const p = layout_payload || {};
  const title = textOrEmpty(p.title || on_screen_text);
  const subtitle = textOrEmpty(p.subtitle);
  return (
    <>
      <Zone yMin={460} yMax={760} width={SHORT_LAYOUT.hookZone.width}>
        {title && <HookTitle accentColor={accentColor}>{title}</HookTitle>}
      </Zone>
      {subtitle && (
        <Zone yMin={820} yMax={980} width={SHORT_LAYOUT.hookZone.width}>
          <BodyText accentColor={accentColor}>{subtitle}</BodyText>
        </Zone>
      )}
    </>
  );
};

// 8.2 short_pain ------------------------------------------------------------
export const ShortPainLayout: React.FC<ShortSceneInput> = ({on_screen_text, caption, layout_payload, accentColor}) => {
  const p = layout_payload || {};
  const title = textOrEmpty(p.title || on_screen_text);
  const body = textOrEmpty(p.body);
  const cap = textOrEmpty(caption);
  return (
    <>
      <Zone yMin={440} yMax={650} width={SHORT_LAYOUT.hookZone.width}>
        {title && <HookTitle fontSize={88} accentColor={accentColor}>{title}</HookTitle>}
      </Zone>
      {body && (
        <Zone yMin={820} yMax={1050} width={SHORT_LAYOUT.bodyZone.width}>
          <BodyText accentColor={accentColor}>{body}</BodyText>
        </Zone>
      )}
      {cap && cap !== body && (
        <Zone yMin={1100} yMax={1300} width={SHORT_LAYOUT.captionZone.width}>
          <CaptionText accentColor={accentColor}>{cap}</CaptionText>
        </Zone>
      )}
    </>
  );
};

// 8.3 short_tip -------------------------------------------------------------
export const ShortTipLayout: React.FC<ShortSceneInput> = ({on_screen_text, caption, layout_payload, accentColor}) => {
  const p = layout_payload || {};
  const title = textOrEmpty(p.title || on_screen_text);
  const body = textOrEmpty(p.body);
  const cap = textOrEmpty(caption);
  return (
    <>
      <Zone yMin={430} yMax={640} width={SHORT_LAYOUT.hookZone.width}>
        {title && <HookTitle fontSize={92} accentColor={accentColor}>{title}</HookTitle>}
      </Zone>
      {body && (
        <Zone yMin={760} yMax={1080} width={SHORT_LAYOUT.bodyZone.width}>
          <BodyText accentColor={accentColor}>{body}</BodyText>
        </Zone>
      )}
      {cap && cap !== body && (
        <Zone yMin={1100} yMax={1320} width={SHORT_LAYOUT.captionZone.width}>
          <CaptionText accentColor={accentColor}>{cap}</CaptionText>
        </Zone>
      )}
    </>
  );
};

// 8.4 short_checklist -------------------------------------------------------
export const ShortChecklistLayout: React.FC<ShortSceneInput> = ({on_screen_text, layout_payload, accentColor}) => {
  const p = layout_payload || {};
  const title = textOrEmpty(p.title || on_screen_text);
  const bullets = (p.bullets || []).filter(Boolean).slice(0, 3);
  return (
    <>
      <Zone yMin={320} yMax={460} width={SHORT_LAYOUT.hookZone.width}>
        {title && <HookTitle fontSize={84} accentColor={accentColor}>{title}</HookTitle>}
      </Zone>
      <Zone yMin={600} yMax={1100} width={SHORT_LAYOUT.bodyZone.width} justify="start">
        {bullets.map((b, i) => (
          <BulletPill key={i} n={i + 1} accentColor={accentColor}>{b}</BulletPill>
        ))}
      </Zone>
    </>
  );
};

// 8.5 short_myth ------------------------------------------------------------
export const ShortMythLayout: React.FC<ShortSceneInput> = ({on_screen_text, layout_payload, accentColor}) => {
  const p = layout_payload || {};
  const title = textOrEmpty(p.title || on_screen_text);
  const body = textOrEmpty(p.body);
  return (
    <>
      <Zone yMin={420} yMax={650} width={SHORT_LAYOUT.hookZone.width}>
        {title && <HookTitle fontSize={88} accentColor={accentColor}>{title}</HookTitle>}
      </Zone>
      {body && (
        <Zone yMin={820} yMax={1120} width={SHORT_LAYOUT.bodyZone.width}>
          <BodyText accentColor={accentColor}>{body}</BodyText>
        </Zone>
      )}
    </>
  );
};

// 8.6 short_quote -----------------------------------------------------------
export const ShortQuoteLayout: React.FC<ShortSceneInput> = ({on_screen_text, layout_payload, accentColor}) => {
  const p = layout_payload || {};
  const title = textOrEmpty(p.title || on_screen_text);
  const body = textOrEmpty(p.body);
  return (
    <Zone yMin={520} yMax={1000} width={920}>
      <div
        style={{
          padding: '40px 56px',
          borderRadius: 36,
          background: 'rgba(11,16,32,0.62)',
          backdropFilter: 'blur(6px)',
          textAlign: 'center',
        }}
      >
        {title && <HookTitle fontSize={84} accentColor={accentColor}>{title}</HookTitle>}
        {body && (
          <div style={{marginTop: 22}}>
            <BodyText accentColor={accentColor}>{body}</BodyText>
          </div>
        )}
      </div>
    </Zone>
  );
};

// 8.7 short_cta -------------------------------------------------------------
export const ShortCtaLayout: React.FC<ShortSceneInput> = ({on_screen_text, layout_payload, accentColor}) => {
  const p = layout_payload || {};
  const title = textOrEmpty(p.title || on_screen_text);
  const cta = textOrEmpty(p.cta);
  return (
    <>
      <Zone yMin={520} yMax={720} width={SHORT_LAYOUT.hookZone.width}>
        {title && <HookTitle fontSize={96} accentColor={accentColor}>{title}</HookTitle>}
      </Zone>
      {cta && (
        <Zone yMin={880} yMax={1080} width={SHORT_LAYOUT.ctaZone.width}>
          <CtaText accentColor={accentColor}>{cta}</CtaText>
        </Zone>
      )}
    </>
  );
};

/** Spec §16.1 layout registry. */
export const SHORT_LAYOUTS: Record<ShortLayoutName, React.FC<ShortSceneInput>> = {
  short_hook: ShortHookLayout,
  short_pain: ShortPainLayout,
  short_tip: ShortTipLayout,
  short_checklist: ShortChecklistLayout,
  short_myth: ShortMythLayout,
  short_quote: ShortQuoteLayout,
  short_cta: ShortCtaLayout,
};

export function pickShortLayout(layout: string | undefined): React.FC<ShortSceneInput> {
  const key = (layout || 'short_tip') as ShortLayoutName;
  return SHORT_LAYOUTS[key] || ShortTipLayout;
}
