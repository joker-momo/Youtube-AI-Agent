/**
 * Eagerly preload the Short composition fonts so Remotion delays render
 * until the webfont CSS + font files are actually ready. Without this,
 * the @import url(...) approach races the render pipeline and the first
 * frames fall back to Arial/Helvetica — which combined with
 * WebkitTextStroke produces a stencil/wood-type look.
 *
 * Loading is side-effect on import; call once from the entry that hosts
 * Short compositions (Root.tsx).
 */
import {loadFont as loadMontserrat} from '@remotion/google-fonts/Montserrat';

// Pull every weight + style the Short layouts actually use.
// Hook/CTA use 900, body uses 800, captions use 800. We load 400 too so
// any inline highlight or fallback weight has a real glyph.
loadMontserrat('normal', {
  weights: ['400', '700', '800', '900'],
  subsets: ['latin', 'latin-ext'],
});

loadMontserrat('italic', {
  weights: ['400', '700', '800', '900'],
  subsets: ['latin', 'latin-ext'],
});
