interface ImportMetaEnv {
  /** Public origin of the FlowState API when it is not served from the frontend's host (build time). */
  readonly VITE_FLOWSTATE_API_URL?: string
  /** Expose window.__fs in production builds for scripted profiling. */
  readonly VITE_FS_DEBUG?: string
}
