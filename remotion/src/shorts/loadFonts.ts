/**
 * Eagerly preload the Short AND long-form composition fonts so Remotion
 * delays render until the webfont CSS + font files are actually ready.
 * Without this, the @import url(...) approach races the render pipeline and
 * the first frames fall back to Arial/Helvetica — which combined with
 * WebkitTextStroke produces a stencil/wood-type look.
 *
 * Loading is side-effect on import; call once from the entry that hosts the
 * compositions (Root.tsx). Both Short and ChannelVideo (long-form) layouts
 * use Montserrat + Manrope, so both are preloaded here.
 */
import {loadFont as loadMontserrat} from '@remotion/google-fonts/Montserrat';
import {loadFont as loadManrope} from '@remotion/google-fonts/Manrope';

// Pull every weight + style the layouts actually use.
// Hook/CTA use 900, body/overlays use 800, long-form also uses 600.
// We load 400 too so any inline highlight or fallback weight has a real glyph.
loadMontserrat('normal', {
  weights: ['400', '600', '700', '800', '900'],
  subsets: ['latin', 'latin-ext'],
});

loadMontserrat('italic', {
  weights: ['400', '600', '700', '800', '900'],
  subsets: ['latin', 'latin-ext'],
});

loadManrope('normal', {
  weights: ['600', '700', '800'],
  subsets: ['latin', 'latin-ext'],
});
