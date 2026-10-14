import React, { useCallback, useEffect, useRef, useState } from 'react'
import { flushSync } from 'react-dom'
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
  const [theme, setThemeState] = useState(storedPreference)
  const activeTransition = useRef(null)
  const generation = useRef(0)
  const systemDark = useMedia('(prefers-color-scheme: dark)')
  const resolvedTheme = theme === 'system' ? (systemDark ? 'dark' : 'light') : theme

  const setTheme = useCallback((next, event) => {
    if (!preferences.has(next)) return
    const root = document.documentElement
    const nextResolved = next === 'system' ? (systemDark ? 'dark' : 'light') : next
    const currentGeneration = ++generation.current
    activeTransition.current?.skipTransition()
    activeTransition.current = null
    delete root.dataset.themeTransition
    const commit = () => {
      if (generation.current !== currentGeneration) return
      root.dataset.theme = nextResolved
      document.querySelector('meta[name="theme-color"]')?.setAttribute(
        'content', nextResolved === 'dark' ? '#161c24' : '#eef3f7',
      )
      setThemeState(next)
    }
    if (!event || nextResolved === resolvedTheme ||
        window.matchMedia('(prefers-reduced-motion: reduce)').matches ||
        typeof document.startViewTransition !== 'function') {
      commit()
      return
    }
    const bounds = event.currentTarget?.getBoundingClientRect()
    const keyboard = event.detail === 0
    const x = Math.min(innerWidth, Math.max(0, keyboard
      ? (bounds ? bounds.left + bounds.width / 2 : innerWidth / 2) : event.clientX))
    const y = Math.min(innerHeight, Math.max(0, keyboard
      ? (bounds ? bounds.top + bounds.height / 2 : innerHeight / 2) : event.clientY))
    const radius = Math.hypot(Math.max(x, innerWidth - x), Math.max(y, innerHeight - y)) + 2
    root.dataset.themeTransition = 'reveal'
    try {
      const transition = document.startViewTransition(() => flushSync(commit))
      activeTransition.current = transition
      transition.ready.then(() => {
        if (generation.current !== currentGeneration) return
        root.animate({ clipPath: [`circle(0px at ${x}px ${y}px)`, `circle(${radius}px at ${x}px ${y}px)`] },
          { duration: 550, easing: 'cubic-bezier(.22,1,.36,1)', pseudoElement: '::view-transition-new(root)' })
      }).catch(() => transition.skipTransition())
      transition.finished.finally(() => {
        if (activeTransition.current === transition) {
          activeTransition.current = null
          delete root.dataset.themeTransition
        }
      }).catch(() => {})
    } catch {
      delete root.dataset.themeTransition
      commit()
    }
  }, [resolvedTheme, systemDark])

  useEffect(() => () => {
    generation.current++
    activeTransition.current?.skipTransition()
    delete document.documentElement.dataset.themeTransition
  }, [])

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
    function snapshotClick(event) {
      // Root snapshots can intercept clicks; preserve only theme controls while revealing.
      if (!activeTransition.current || event.target !== document.documentElement) return
      const button = [...document.querySelectorAll('.theme-toggle, .theme-option')].find(node => {
        const box = node.getBoundingClientRect()
        return box.width && box.height && event.clientX >= box.left && event.clientX <= box.right &&
          event.clientY >= box.top && event.clientY <= box.bottom
      })
      if (!button) return
      event.preventDefault()
      const next = button.dataset.themeValue || (document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark')
      setTheme(next, { currentTarget: button, clientX: event.clientX, clientY: event.clientY, detail: event.detail })
    }
    window.addEventListener('storage', sync)
    document.addEventListener('click', snapshotClick)
    return () => {
      window.removeEventListener('storage', sync)
      document.removeEventListener('click', snapshotClick)
    }
  }, [setTheme])

  return { theme, resolvedTheme, setTheme }
}

export function ThemeToggle({ resolvedTheme, onChange }) {
  const dark = resolvedTheme === 'dark'
  const label = dark ? '切换到浅色模式' : '切换到深色模式'
  const Icon = dark ? Sun : Moon
  return (
    <button type="button" className="theme-toggle" aria-label={label}
      title={label} aria-pressed={dark} onClick={(event) => onChange(dark ? 'light' : 'dark', event)}>
      <Icon size={16} aria-hidden="true" />
      <span>{dark ? '浅色' : '深色'}</span>
    </button>
  )
}
