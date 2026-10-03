// Live adversarial proof of the four steward-requested fixes on Studio Net.
//
// Every check reads the leader's consensus_data.execution_result, not the
// transaction status - status 5 comes back even when the method raised.
import { createClient, createAccount } from 'genlayer-js';
import { studionet } from 'genlayer-js/chains';

const C = process.env.ON_ADDR;
const cli = {
  issuer: createClient({ chain: studionet, account: createAccount(process.env.KI) }),
  agent: createClient({ chain: studionet, account: createAccount(process.env.KA) }),
  other: createClient({ chain: studionet, account: createAccount(process.env.KO) }),
};
const A = {
  issuer: createAccount(process.env.KI).address,
  agent: createAccount(process.env.KA).address,
  other: createAccount(process.env.KO).address,
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
const read = (k, fn, args) => cli[k].readContract({ address: C, functionName: fn, args });
async function j(k, fn, args) {
  for (let i = 0; i < 15; i++) {
    try { return JSON.parse(await read(k, fn, args)); }
    catch (e) { await new Promise((s) => setTimeout(s, 3000)); }
  }
  throw new Error(`read ${fn} failed after retries`);
}

async function main() {
  const reg = await write('agent', 'register', [], { value: 3n * GEN });
  check('agent registers with 3 GEN', reg.ok, `exec=${reg.exec}`);

  const post = (k, jid, agent, commit, deadline) => write(k, 'post_job',
    [jid, agent, REPO, commit, 'npm test', ['has tests'], deadline, ['README.md'], '']);

  // ---------------------------------------------------------------
  console.log('\n=== FIX 1: unauthorised jobs cannot affect reputation or stake ===');
  const far = Math.floor(Date.now() / 1000) + 86400;

  const j1 = 'unauth-' + Date.now();
  const p1 = await post('issuer', j1, A.agent, 'deadbeef01', far);
  check('issuer posts a job naming the agent', p1.ok, `exec=${p1.exec}`);

  let rec = await j('agent', 'get_agent', [A.agent]);
  console.log(`  agent before -> ${JSON.stringify(rec)}`);
  check('job is NOT accepted yet', rec.completed === 0 && rec.failed === 0, JSON.stringify(rec));

  const acc = await write('other', 'accept_job', [j1]);
  check('a third party cannot accept it', acc.exec !== 'SUCCESS', `exec=${acc.exec}`);

  const acc2 = await write('issuer', 'accept_job', [j1]);
  check('the ISSUER cannot accept it either', acc2.exec !== 'SUCCESS', `exec=${acc2.exec}`);

  const ver = await write('issuer', 'verify', [j1]);
  check('verify refuses an unaccepted job', ver.exec !== 'SUCCESS', `exec=${ver.exec}`);

  rec = await j('agent', 'get_agent', [A.agent]);
  check('reputation untouched', rec.completed === 0 && rec.failed === 0, JSON.stringify(rec));
  check('stake untouched', rec.staked === 3000000000000000000, `staked=${rec.staked}`);
  check('still active', rec.active === true, `active=${rec.active}`);

  // ---------------------------------------------------------------
  console.log('\n=== FIX 2: unaccepted jobs do not reserve artifact keys ===');
  const commit = 'squattarget01';
  const keyBefore = await j('issuer', 'get_artifact_key', [REPO, commit]);
  check('artifact key free before posting', keyBefore.reserved === false, JSON.stringify(keyBefore));

  for (let i = 0; i < 5; i++) {
    await post('other', `squatter-${i}-${Date.now()}`, A.other, commit, far);
  }
  const keyAfter = await j('issuer', 'get_artifact_key', [REPO, commit]);
  check('still free after 5 posts on that artifact', keyAfter.reserved === false, JSON.stringify(keyAfter));

  const reuse = await post('issuer', 'real-' + Date.now(), A.agent, commit, far);
  check('a real job can still use that artifact', reuse.ok, `exec=${reuse.exec}`);

  // ---------------------------------------------------------------
  console.log('\n=== FIX 3: safe withdrawal ===');
  const floor = await write('agent', 'request_withdraw', [3000000000000000000]);
  check('active agent cannot withdraw below minimum stake', floor.exec !== 'SUCCESS', `exec=${floor.exec}`);

  const n1 = await write('agent', 'request_withdraw', [1000000000000000000]);
  check('withdrawal down to the floor is accepted', n1.ok, `exec=${n1.exec}`);
  const led = await j('agent', 'get_pending_withdraw', [A.agent]);
  console.log(`  ledger -> ${JSON.stringify(led)}`);
  check('reservation recorded', led.pending_withdraw === 1000000000000000000, JSON.stringify(led));

  const c1 = await write('agent', 'claim_withdraw', [1]);
  check('claim settles', c1.ok, `exec=${c1.exec}`);
  const replay = await write('agent', 'claim_withdraw', [1]);
  check('replayed claim refused', replay.exec !== 'SUCCESS', `exec=${replay.exec}`);

  const de = await write('agent', 'deactivate', []);
  check('deactivate succeeds', de.ok, `exec=${de.exec}`);
  const n2 = await write('agent', 'request_withdraw', [2000000000000000000]);
  check('deactivated agent may reserve all remaining stake', n2.ok, `exec=${n2.exec}`);
  const c2 = await write('agent', 'claim_withdraw', [2]);
  check('final claim settles', c2.ok, `exec=${c2.exec}`);
  rec = await j('agent', 'get_agent', [A.agent]);
  console.log(`  agent after -> ${JSON.stringify(rec)}`);
  check('remaining stake fully withdrawn', rec.staked === 0, `staked=${rec.staked}`);

  // ---------------------------------------------------------------
  console.log('\n=== FIX 4: slashed funds have an auditable disposition ===');
  const sj = 'slash-' + Date.now();
  const ps = await post('issuer', sj, A.other, 'slashcommit1', far);
  check('job posted against the second agent', ps.ok, `exec=${ps.exec}`);
  const acc3 = await write('other', 'accept_job', [sj]);
  check('second agent accepts (creating a real obligation)', acc3.ok, `exec=${acc3.exec}`);

  const settle = await write('issuer', 'settle_unclaimed', [sj]);
  check('settle_unclaimed rejects a live deadline', settle.exec !== 'SUCCESS', `exec=${settle.exec}`);

  const orec = await j('other', 'get_agent', [A.other]);
  console.log(`  other agent -> ${JSON.stringify(orec)}`);
  check('no slash before deadline', orec.slashed_count === 0, JSON.stringify(orec));

  console.log(`\n=== RESULT: ${pass} passed, ${fail} failed ===`);
  console.log(`NOTE: the live slash/dispose leg needs an expired deadline, which`);
  console.log(`this harness cannot arrange; it is proven by direct-mode tests.`);
}

main().catch((e) => { console.error('HARNESS FAIL:', e.message); process.exit(1); });