// Upstreams can repeat a model. Full IDs must stay unique for React row keys.
export function uniqueModels(models) {
  const catalog = new Map()
  for (const model of Array.isArray(models) ? models : []) {
    if (typeof model?.id !== 'string' || !model.id || catalog.has(model.id)) continue
    catalog.set(model.id, model)
  }
  return [...catalog.values()]
}
