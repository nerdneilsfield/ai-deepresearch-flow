// Type declarations for @fontsource self-hosted font packages.
// These packages ship CSS that we import for side effects only;
// there is no runtime JS export to consume.
declare module '@fontsource-variable/*' {
  const content: string
  export default content
}

declare module '@fontsource/*' {
  const content: string
  export default content
}
