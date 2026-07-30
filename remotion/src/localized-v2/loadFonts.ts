import {loadFont as loadManrope} from '@remotion/google-fonts/Manrope';
import {loadFont as loadMontserrat} from '@remotion/google-fonts/Montserrat';
import {
  getInfo as getNotoSansJPInfo,
  loadFont as loadNotoSansJP,
} from '@remotion/google-fonts/NotoSansJP';
import {
  getInfo as getNotoSansKRInfo,
  loadFont as loadNotoSansKR,
} from '@remotion/google-fonts/NotoSansKR';

loadMontserrat('normal', {
  weights: ['400', '600', '700', '800', '900'],
  subsets: ['latin', 'latin-ext'],
});
loadManrope('normal', {
  weights: ['600', '700', '800'],
  subsets: ['latin', 'latin-ext'],
});
type FontInfo = ReturnType<typeof getNotoSansJPInfo>;

const codepointInRange = (codepoint: number, expression: string): boolean =>
  expression.split(',').some((rawPart) => {
    const part = rawPart.trim().replace(/^U\+/i, '');
    if (part.includes('?')) {
      const start = Number.parseInt(part.replace(/\?/g, '0'), 16);
      const end = Number.parseInt(part.replace(/\?/g, 'F'), 16);
      return codepoint >= start && codepoint <= end;
    }
    const [startRaw, endRaw] = part.split('-');
    const start = Number.parseInt(startRaw ?? '', 16);
    const end = Number.parseInt(endRaw ?? startRaw ?? '', 16);
    return codepoint >= start && codepoint <= end;
  });

export const fontSubsetsForText = (
  text: string,
  info: FontInfo,
): string[] => {
  const subsets = new Set<string>();
  for (const char of text) {
    const codepoint = char.codePointAt(0);
    if (codepoint === undefined) {
      continue;
    }
    const match = Object.entries(info.unicodeRanges).find(([_key, range]) =>
      codepointInRange(codepoint, range),
    );
    if (match) {
      subsets.add(match[0]);
    }
  }
  return [...subsets];
};

const loaded = new Set<string>();

export const ensureLocaleFont = (locale: string, visibleText: string): void => {
  if (locale !== 'ko-KR' && locale !== 'ja-JP') {
    return;
  }
  const info = locale === 'ko-KR' ? getNotoSansKRInfo() : getNotoSansJPInfo();
  const subsets = fontSubsetsForText(visibleText, info);
  const key = `${locale}:${subsets.join(',')}`;
  if (subsets.length === 0 || loaded.has(key)) {
    return;
  }
  loaded.add(key);
  if (locale === 'ko-KR') {
    loadNotoSansKR('normal', {
      weights: ['700'],
      // Runtime supports generated CJK chunk keys such as "[42]"; the package's
      // public union exposes only the meta-subset name.
      subsets: subsets as unknown as ['korean'],
      ignoreTooManyRequestsWarning: true,
    });
  } else {
    loadNotoSansJP('normal', {
      weights: ['700'],
      subsets: subsets as unknown as ['japanese'],
      ignoreTooManyRequestsWarning: true,
    });
  }
};

export const localeFontFamily = (locale: string): string => {
  if (locale === 'ko-KR') {
    return '"Noto Sans KR", "Apple SD Gothic Neo", sans-serif';
  }
  if (locale === 'ja-JP') {
    return '"Noto Sans JP", "Hiragino Sans", sans-serif';
  }
  return 'Manrope, Montserrat, "Helvetica Neue", sans-serif';
};
