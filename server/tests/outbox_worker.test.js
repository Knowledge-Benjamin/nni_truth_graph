const test = require('node:test');
const assert = require('node:assert/strict');
const { getReviewResolutionUpdate } = require('../outbox_worker');

test('approval re-enters the pipeline at stage 6 dedup', () => {
  const update = getReviewResolutionUpdate('APPROVE');

  assert.match(update.sql, /STAGE_6_DEDUP/);
  assert.match(update.sql, /status = 'PROCESSING'/);
  assert.equal(update.status, 'PROCESSING');
  assert.equal(update.pipelineStage, 'STAGE_6_DEDUP');
});

test('rejection keeps the claim out of mutation', () => {
  const update = getReviewResolutionUpdate('REJECT');

  assert.match(update.sql, /status = 'AUTO_REJECT'/);
  assert.equal(update.pipelineStage, null);
});
