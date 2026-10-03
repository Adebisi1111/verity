// Live proof of the slash + disposal leg (fix 4).
//
// The earlier harness could not reach this: settle_unclaimed needs an EXPIRED
// deadline, and post_job rejects a deadline already in the past. So post with
// a short deadline and actually wait for it to lapse.
import { createClient, createAccount } from 'genlayer-js';
import { studionet } from 'genlayer-js/chains';

const C = process.env.ON_ADDR;
const cli = {
  issuer: createClient({ chain: studionet, account: createAccount(process.env.KI) }),
  agent: createClient({ chain: studionet, account: createAccount(process.env.KA) }),
};
const A = {
  issuer: createAccount(process.env.KI).address,
  agent: createAccount(process.env.KA).address,
};
const GEN = 10n ** 18n;
const REPO = 'https://github.com/genlayerlabs/genlayer-js';

let pass = 0, fail = 0;
const check = (n, c, d = '') => { c ? (pass++, console.log(`  PASS  ${n}`)) : (fail++, console.log(`  FAIL  ${n} ${d}`)); };

async function execResult(hash) {
  // The explorer intermittently serves an HTML error page and may not have
  // indexed the tx yet; retry rather than misreading that as a contract result.
  for (let i = 0; i < 20; i++) {
    try {
      const r = await fetch(`https://explorer-studio.genlayer.com/api/transactions/${hash}`);
      const t = await r.text();
      if (t.trim().startsWith('<')) throw new Error('html error page');
      const j = JSON.parse(t);
      const lr = j?.transaction?.consensus_data?.leader_receipt;
      const e = Array.isArray(lr) ? lr[0] : lr;
      if (e?.execution_result && e.execution_result !== 'UNKNOWN') return e.execution_result;
    } catch (err) { /* retry */ }
    await new Promise((s) => setTimeout(s, 4000));
  }
  return 'UNKNOWN';
}
async function write(k, fn, args, opts = {}) {
  const hash = await cli[k].writeContract({ address: C, functionName: fn, args, ...opts });
  await cli[k].waitForTransactionReceipt({ hash, waitUntil: 'decided', retries: 300, interval: 3000 });
  const exec = await execResult(hash);
  return { hash, exec, ok: exec === 'SUCCESS' };
}
async function j(k, fn, args) {
  for (let i = 0; i < 15; i++) {
    try { return JSON.parse(await cli[k].readContract({ address: C, functionName: fn, args })); }
    catch (e) { await new Promise((s) => setTimeout(s, 3000)); }
  }
  throw new Error(`read ${fn} failed after retries`);
}

async function main() {
  const stamp = Date.now();
  const tag = `vyslash-${stamp}`;

  const reg = await write('agent', 'register', [], { value: 3n * GEN });
  check('agent registers with 3 GEN', reg.ok, `exec=${reg.exec}`);

  // A deadline far enough ahead for post_job to accept, short enough to wait out.
  const deadline = Math.floor(Date.now() / 1000) + 90;

  const p = await write('issuer', 'post_job',
    [tag, A.agent, REPO, `commit${stamp}`, 'npm test', ['has tests'], deadline, ['README.md'], '']);
  check('issuer posts a short-deadline job', p.ok, `exec=${p.exec}`);

  const acc = await write('agent', 'accept_job', [tag]);
  check('agent accepts - a real obligation now exists', acc.ok, `exec=${acc.exec}`);

  const early = await write('issuer', 'settle_unclaimed', [tag]);
  check('cannot settle while the deadline is live', early.exec !== 'SUCCESS', `exec=${early.exec}`);

  console.log(`  waiting out the ${90}s deadline...`);
  await new Promise((r) => setTimeout(r, 100000));

  const s = await write('issuer', 'settle_unclaimed', [tag]);
  check('settle_unclaimed succeeds once expired', s.ok, `exec=${s.exec}`);

  const rec = await j('agent', 'get_agent', [A.agent]);
  console.log(`  agent after slash -> ${JSON.stringify(rec)}`);
  // Keep the arithmetic in BigInt and convert only for comparison - mixing
  // BigInt and Number here throws at runtime.
  const burnedBig = 3000000000000000000n * 10n / 100n;
  const burned = Number(burnedBig);
  const remaining = Number(3000000000000000000n - burnedBig);
  check('agent was slashed', rec.slashed_count === 1, JSON.stringify(rec));
  check('10% of stake burned', rec.slashed_total === burned, `${rec.slashed_total} vs ${burned}`);
  check('burned value sits in an auditable pool', rec.slashed_pool === rec.slashed_total, JSON.stringify(rec));

  const led = await j('agent', 'get_pending_withdraw', [A.agent]);
  console.log(`  ledger -> ${JSON.stringify(led)}`);
  check('pool is readable on-chain', led.slashed_pool === rec.slashed_total, JSON.stringify(led));
  // slashed_sink is a NETWORK-WIDE lifetime total, so only the delta is
  // attributable to this run - earlier runs already contributed to it.
  const sinkBefore = led.slashed_sink;

  const d = await write('agent', 'dispose_slashed', []);
  check('dispose_slashed succeeds on a real pool', d.ok, `exec=${d.exec}`);

  const led2 = await j('agent', 'get_pending_withdraw', [A.agent]);
  console.log(`  ledger after -> ${JSON.stringify(led2)}`);
  check('pool emptied', led2.slashed_pool === 0, JSON.stringify(led2));
  check('value moved to the network sink', led2.slashed_sink - sinkBefore === burned,
    `${sinkBefore} -> ${led2.slashed_sink} (delta ${led2.slashed_sink - sinkBefore} vs ${burned})`);

  const d2 = await write('agent', 'dispose_slashed', []);
  check('second dispose refused - one-shot', d2.exec !== 'SUCCESS', `exec=${d2.exec}`);

  const rec2 = await j('agent', 'get_agent', [A.agent]);
  check('burned value never returned to stake', rec2.staked === remaining, `${rec2.staked} vs ${remaining}`);
  check('slashed_total retained as a permanent record', rec2.slashed_total === burned, JSON.stringify(rec2));

  console.log(`\n=== RESULT: ${pass} passed, ${fail} failed ===`);
}

main().catch((e) => { console.error('HARNESS FAIL:', e.message); console.error(e.stack); process.exit(1); });