import test from 'node:test'
import assert from 'node:assert/strict'
import { uniqueModels } from '../src/model-list.js'

test('repeated catalog IDs render once and keep the first model metadata', () => {
  const first = { id: 'trae/glm-5.2', context_window: 32768 }
  const input = [first, { id: first.id, context_window: 8192 }, { id: 'trae/DeepSeek-V4-Flash' }, first]
  assert.deepEqual(uniqueModels(input), [first, input[2]])
  assert.equal(input.length, 4)
})

test('the full model ID determines identity across platforms and letter case', () => {
  const input = [{ id: 'trae/glm' }, { id: 'codebuddy/glm' }, { id: 'trae/GLM' }]
  assert.deepEqual(uniqueModels(input), input)
})

test('empty and malformed catalog entries cannot become unstable table rows', () => {
  assert.deepEqual(uniqueModels(undefined), [])
  assert.deepEqual(uniqueModels(null), [])
  assert.deepEqual(uniqueModels({}), [])
  assert.deepEqual(uniqueModels([null, {}, { id: '' }, { id: 12 }, { id: 'qoder/auto' }]), [{ id: 'qoder/auto' }])
})
