(() => {
  let preference = 'system'
  try {
    const saved = localStorage.getItem('unified-theme')
    if (['light', 'dark', 'system'].includes(saved)) preference = saved
  } catch {}
  const theme = preference === 'system'
    ? (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')
    : preference
  document.documentElement.dataset.theme = theme
  document.querySelector('meta[name="theme-color"]').content = theme === 'dark' ? '#161c24' : '#eef3f7'
})()
