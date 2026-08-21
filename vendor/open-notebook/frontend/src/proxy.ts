import { NextResponse } from 'next/server'
import type { NextRequest } from 'next/server'

export function proxy(request: NextRequest) {
  const { pathname } = request.nextUrl
  // pathname is basePath-stripped when NEXT_BASE_PATH is set
  const base = process.env.NEXT_BASE_PATH?.replace(/\/$/, "") || ""

  // Redirect app root to notebooks
  if (pathname === "/" || pathname === "") {
    return NextResponse.redirect(new URL(`${base}/notebooks`, request.url))
  }

  return NextResponse.next()
}

export const config = {
  matcher: [
    '/((?!api|_next/static|_next/image|favicon.ico).*)',
  ],
}
