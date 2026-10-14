import React, { useEffect, useState } from 'react'
import { Moon, Sun } from 'lucide-react'
import { useMedia } from './motion'

const preferences = new Set(['light', 'dark', 'system'])
function storedPreference() {
  try {
    const value = window.localStorage.getItem('unified-theme')
    return preferences.has(value) ? value : 'system'
  } catch {
    return 'system'
  }
}

export function useTheme() {
  const [theme, setTheme] = useState(storedPreference)
  const systemDark = useMedia('(prefers-color-scheme: dark)')
  const resolvedTheme = theme === 'system' ? (systemDark ? 'dark' : 'light') : theme

  useEffect(() => {
    document.documentElement.dataset.theme = resolvedTheme
    document.querySelector('meta[name="theme-color"]')?.setAttribute(
      'content', resolvedTheme === 'dark' ? '#161c24' : '#eef3f7',
    )
    try {
      window.localStorage.setItem('unified-theme', theme)
    } catch {
      // Theme switching still works when browser storage is unavailable.
    }
  }, [theme, resolvedTheme])

  useEffect(() => {
    function sync(event) {
      if (event.key === 'unified-theme' || event.key === null) setTheme(storedPreference())
    }
    window.addEventListener('storage', sync)
    return () => window.removeEventListener('storage', sync)
  }, [])

  return { theme, resolvedTheme, setTheme }
}

export function ThemeToggle({ resolvedTheme, onChange }) {
  const dark = resolvedTheme === 'dark'
  const label = dark ? '切换到浅色模式' : '切换到深色模式'
  const Icon = dark ? Sun : Moon
  return (
    <button type="button" className="theme-toggle" aria-label={label}
      title={label} aria-pressed={dark} onClick={() => onChange(dark ? 'light' : 'dark')}>
      <Icon size={16} aria-hidden="true" />
      <span>{dark ? '浅色' : '深色'}</span>
    </button>
  )
}
