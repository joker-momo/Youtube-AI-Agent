import type {RenderProps} from '../render-props';

export const localizedV2Locales = [
  'en-US',
  'fr-FR',
  'pt-BR',
  'ko-KR',
  'ja-JP',
] as const;

export type LocalizedV2Locale = (typeof localizedV2Locales)[number];

export type LocalizedV2RenderProps = RenderProps & {
  schemaVersion: 'localized-render-props-v2/v1';
  locale: LocalizedV2Locale;
  composition: 'LocalizedV2ChannelVideo';
  branding: NonNullable<RenderProps['branding']> & {
    intro_video_path: string;
    disclaimer_video_path: string;
    outro_video_path: string;
    intro_sec: number;
    disclaimer_sec: number;
    outro_sec: number;
    hybrid_card_bg: string;
  };
};

const nonEmpty = (value: unknown): value is string =>
  typeof value === 'string' && value.trim().length > 0;

const positive = (value: unknown): value is number =>
  typeof value === 'number' && Number.isFinite(value) && value > 0;

const safePublicPath = (value: unknown): value is string =>
  nonEmpty(value) &&
  !value.startsWith('/') &&
  !value.includes('://') &&
  !value.split('/').includes('..');

export const assertLocalizedV2Props = (
  value: LocalizedV2RenderProps,
): LocalizedV2RenderProps => {
  if (
    value.schemaVersion !== 'localized-render-props-v2/v1' ||
    value.composition !== 'LocalizedV2ChannelVideo' ||
    !localizedV2Locales.includes(value.locale)
  ) {
    throw new Error('Invalid localized V2 render-props envelope');
  }
  if (value.render.subtitles?.enabled !== false) {
    throw new Error('Localized V2 subtitles must remain disabled');
  }
  if (value.audio.music !== null || !safePublicPath(value.audio.narration)) {
    throw new Error('Localized V2 audio must contain narration only');
  }
  const branding = value.branding;
  for (const path of [
    branding.intro_video_path,
    branding.disclaimer_video_path,
    branding.outro_video_path,
    branding.hybrid_card_bg,
  ]) {
    if (!safePublicPath(path)) {
      throw new Error('Localized V2 requires replaceable brand media');
    }
  }
  for (const duration of [
    branding.intro_sec,
    branding.disclaimer_sec,
    branding.outro_sec,
  ]) {
    if (!positive(duration)) {
      throw new Error('Localized V2 brand media requires positive duration');
    }
  }
  if (value.scenes.length === 0) {
    throw new Error('Localized V2 requires at least one scene');
  }
  for (const scene of value.scenes) {
    if (
      !safePublicPath(scene.asset_refs?.background) ||
      scene.asset_refs.background_media_kind !== 'video'
    ) {
      throw new Error(`Localized V2 scene ${scene.id} requires video backing`);
    }
    if (scene.caption !== '' || (scene.word_segments?.length ?? 0) > 0) {
      throw new Error('Localized V2 scenes cannot contain subtitle artifacts');
    }
    if (
      scene.visual_type === 'graphic' &&
      !safePublicPath(scene.graphic?.image_ref)
    ) {
      throw new Error(`Localized V2 graphic scene ${scene.id} needs an image`);
    }
  }
  return value;
};
