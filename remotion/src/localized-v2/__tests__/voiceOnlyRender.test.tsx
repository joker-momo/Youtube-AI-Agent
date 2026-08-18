import {
  defaultLocalizedV2Props,
  calculateLocalizedV2Metadata,
} from '../Root';
import {getInfo as getNotoSansJPInfo} from '@remotion/google-fonts/NotoSansJP';
import {getInfo as getNotoSansKRInfo} from '@remotion/google-fonts/NotoSansKR';
import {fontSubsetsForText} from '../loadFonts';
import {localizedTimeline} from '../LocalizedV2ChannelVideo';
import {assertLocalizedV2Props, localizedV2Locales} from '../types';

const fail = (message: string): never => {
  throw new Error(message);
};

for (const locale of localizedV2Locales) {
  const props = {
    ...defaultLocalizedV2Props,
    locale,
  };
  const valid = assertLocalizedV2Props(props);
  valid.render.subtitles?.enabled === false ||
    fail(`${locale} enabled subtitles`);
  valid.audio.music === null || fail(`${locale} attached music`);
  valid.scenes.every(
    (scene) =>
      scene.caption === '' &&
      (scene.word_segments?.length ?? 0) === 0 &&
      scene.asset_refs.background_media_kind === 'video',
  ) || fail(`${locale} violated the voice-only scene contract`);
}

const metadata = calculateLocalizedV2Metadata({
  props: defaultLocalizedV2Props,
  defaultProps: defaultLocalizedV2Props,
  abortSignal: new AbortController().signal,
  compositionId: 'LocalizedV2ChannelVideo',
  isRendering: false,
}) as {durationInFrames?: number};
metadata.durationInFrames === 120 || fail('metadata ignored the compiled duration');

let rejected = false;
try {
  assertLocalizedV2Props({
    ...defaultLocalizedV2Props,
    render: {
      ...defaultLocalizedV2Props.render,
      subtitles: {enabled: true},
    },
  });
} catch {
  rejected = true;
}
rejected || fail('subtitle-enabled props did not fail closed');
fontSubsetsForText('건강한 노화', getNotoSansKRInfo()).length > 0 ||
  fail('Korean glyph chunks were not resolved');
fontSubsetsForText('健やかな毎日', getNotoSansJPInfo()).length > 0 ||
  fail('Japanese glyph chunks were not resolved');
const originalTimeline = localizedTimeline(defaultLocalizedV2Props, 30);
const longerDisclaimer = localizedTimeline({
  ...defaultLocalizedV2Props,
  branding: {
    ...defaultLocalizedV2Props.branding,
    disclaimer_sec: defaultLocalizedV2Props.branding.disclaimer_sec + 2,
  },
}, 30);
longerDisclaimer.contentFrom - originalTimeline.contentFrom === 60 ||
  fail('disclaimer duration did not shift content by only its own delta');
longerDisclaimer.contentFrames === originalTimeline.contentFrames ||
  fail('disclaimer duration changed the content timeline');

process.stdout.write('localized-v2 voice-only render contract passed\n');
