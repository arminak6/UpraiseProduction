import {mathjax} from '@mathjax/src/js/mathjax.js';
import {TeX} from '@mathjax/src/js/input/tex.js';
import {liteAdaptor} from '@mathjax/src/js/adaptors/liteAdaptor.js';
import {RegisterHTMLHandler} from '@mathjax/src/js/handlers/html.js';
import {SerializedMmlVisitor} from '@mathjax/src/js/core/MmlTree/SerializedMmlVisitor.js';
import {STATE} from '@mathjax/src/js/core/MathItem.js';

import '@mathjax/src/components/require.mjs';
import {
  setupEngine,
  engineReady,
  toSpeech,
} from 'speech-rule-engine/js/common/system.js';

import '@mathjax/src/js/input/tex/base/BaseConfiguration.js';
import '@mathjax/src/js/input/tex/ams/AmsConfiguration.js';
import '@mathjax/src/js/input/tex/newcommand/NewcommandConfiguration.js';
import '@mathjax/src/js/input/tex/noundefined/NoUndefinedConfiguration.js';

const MAX_FORMULAS = 100;
const MAX_FORMULA_CHARACTERS = 12_000;

function fail(message) {
  process.stdout.write(JSON.stringify({error: message, results: []}));
  process.exitCode = 1;
}

async function readInput() {
  const chunks = [];
  for await (const chunk of process.stdin) {
    chunks.push(chunk);
  }
  return JSON.parse(Buffer.concat(chunks).toString('utf8'));
}

try {
  const request = await readInput();
  const formulas = request?.formulas;
  const locale = typeof request?.locale === 'string' ? request.locale : 'en';
  const domain = typeof request?.domain === 'string' ? request.domain : 'clearspeak';
  if (!Array.isArray(formulas) || formulas.length > MAX_FORMULAS) {
    fail('The formulas field must be an array with at most 100 entries.');
  } else if (
    formulas.some(
      (formula) =>
        typeof formula !== 'string' || formula.length > MAX_FORMULA_CHARACTERS,
    )
  ) {
    fail('Every formula must be a string no longer than 12000 characters.');
  } else {
    const adaptor = liteAdaptor({fontSize: 16});
    RegisterHTMLHandler(adaptor);
    const tex = new TeX({
      packages: ['base', 'ams', 'newcommand', 'noundefined'],
      formatError(_jax, error) {
        throw error;
      },
    });
    const html = mathjax.document('', {InputJax: tex});
    const visitor = new SerializedMmlVisitor();
    const toMathML = (node) => visitor.visitTree(node, html);

    await setupEngine({locale, modality: 'speech', domain});
    await engineReady();

    const results = formulas.map((formula) => {
      try {
        const mathml = html.convert(formula, {
          display: true,
          em: 16,
          ex: 8,
          containerWidth: 1280,
          end: STATE.CONVERT,
        });
        const speech = toSpeech(toMathML(mathml)).trim();
        if (!speech) {
          throw new Error('Speech Rule Engine returned no narration.');
        }
        return {speech};
      } catch (error) {
        return {
          error: error instanceof Error ? error.message : 'Formula conversion failed.',
        };
      }
    });
    process.stdout.write(JSON.stringify({results}));
  }
} catch (error) {
  fail(error instanceof Error ? error.message : 'Invalid converter request.');
}
