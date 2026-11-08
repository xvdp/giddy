"""Unit detection and code-only equivalence for the token-based adapters."""
from __future__ import annotations

import pytest

from giddy.lang import adapter_for


def keys(path: str, src: str) -> list[str]:
    units = adapter_for(path).units(src, True)
    assert units is not None, "parse failed"
    return [u.key for u in units]


def same(path: str, a: str, b: str) -> bool:
    """True when every unit of a and b has an equal code-only signature."""
    ua, ub = adapter_for(path).units(a, True), adapter_for(path).units(b, True)
    return {u.key: u.signature for u in ua} == {u.key: u.signature for u in ub}


RUST = '''\
//! crate doc
use std::fmt;

/// doc
#[derive(Debug)]
pub struct Foo<T> { a: T }

impl<T: fmt::Debug> fmt::Display for Foo<T> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let s = r#"a "{" brace"#;
        let c = '{';
        write!(f, "{:?} {}", self.a, s)
    }
}

impl<T> Foo<T> {
    pub(crate) fn new(a: T) -> Self where T: Clone { Foo { a } }
}

mod tests {
    #[test]
    fn t() { assert_eq!(1, 1); }
}
'''

CPP = '''\
#include <vector>
namespace a::b {
template <typename T>
class Box : public Base<T> {
  public:
    Box(int x) : x_(x), y_{2} { init(); }
    ~Box() {}
    bool operator==(const Box& o) const noexcept { return x_ == o.x_; }
    int get() const { return x_; }
  private:
    int x_;
};
int Box::size(int n) { return n; }
#if FAST
int fast(int x) {
#else
int fast(long x) {
#endif
    return x;
}
}  // namespace a::b
extern "C" { void c_api(void) { } }
DECLARE_THING(x)
typedef struct { int a; } Pod;
'''

TS = '''\
import { a, b } from "./x"
export interface Shape { area(): number }
export class Square implements Shape {
  private side = 1
  constructor(side: number) { this.side = side }
  area(): number { return this.side ** 2 }
  handler = (e: Event) => { console.log(`x ${e.type} ${`nested ${1 / 2}`}`) }
}
export function load(p: string): Promise<{ data: string[] }> {
  const re = /[{]+/g
  return fetch(p).then(r => r.json())
}
export const twice = async (x: number) => {
  return x * 2
}
const config = { a: 1, b() { return 2 } }
'''

SLANG = '''\
import common;
struct Ray : IRay { float3 o; float3 d; float3 at(float t) { return o + t * d; } };
cbuffer Params : register(b0) { float4x4 view; }
[shader("compute")]
[numthreads(8, 8, 1)]
void main(uint3 id : SV_DispatchThreadID) { }
float4 frag(VSOut i) : SV_Target { return i.color; }
T sum<T : IArithmetic>(T a, T b) { return a + b; }
'''

GLSL = '''\
#version 450
layout(local_size_x = 256) in;
layout(std430, binding = 0) buffer Data { float v[]; } data;
struct Light { vec3 p; };
float shade(Light l) { return l.p.x; }
void main() {
    data.v[gl_GlobalInvocationID.x] *= 2.0;
}
'''

C = '''\
#include <stdio.h>
/* block */
static int counter = 0;
struct node { int v; struct node *next; };
struct node *push(struct node *head, int v) {
    return head;
}
GLOBAL(void)
jinit(int x)
{
    counter += x;
}
'''

JULIA = '''\
module Grids
using LinearAlgebra
export Grid

"""
    Grid

A grid.
"""
struct Grid{T} <: AbstractGrid
    n::Int
end

"docstring"
function spacing(g::Grid, i)
    x = [i for i in 1:g.n]
    return x[end] - x[begin]
end

@inline area(g::Grid) = g.n^2
Base.size(g::Grid)::Int where T = g.n
spacing(g::Grid) = spacing(g, 1)
b′ = transpose(a)'
macro twice(ex)
    quote $ex; $ex end
end
end # module
'''


@pytest.mark.parametrize("path,src,expected", [
    ("a.rs", RUST, ["<module>", "<Foo as fmt::Display>", "<Foo as fmt::Display>::fmt", "Foo", "Foo::new",
                    "tests", "tests::t"]),
    ("a.cpp", CPP, ["<module>", "a::b", "a::b::Box", "a::b::Box::Box", "a::b::Box::~Box",
                    "a::b::Box::operator==", "a::b::Box::get", "a::b::Box::size", "a::b::fast",
                    "c_api", 'extern "C"']),
    ("a.ts", TS, ["<module>", "Shape", "Square", "Square.constructor", "Square.area", "Square.handler",
                  "load", "twice"]),
    ("a.slang", SLANG, ["<module>", "Ray", "Ray.at", "main", "frag", "sum"]),
    ("a.glsl", GLSL, ["<module>", "Light", "shade", "main"]),
    ("a.c", C, ["<module>", "node", "push", "jinit"]),
    ("a.jl", JULIA, ["Grids", "Grids.Grid", "Grids.spacing(g :: Grid , i)", "Grids.area", "Grids.Base.size",
                     "Grids.spacing(g :: Grid)", "Grids.twice"]),
])
def test_units(path, src, expected):
    assert keys(path, src) == expected


def test_comment_and_format_changes_are_equal():
    assert same("a.rs", RUST, RUST.replace("/// doc", "/// other doc").replace("{ a: T }", "{\n    a: T,\n}".replace(",", "")))
    assert same("a.cpp", CPP, CPP.replace("int get() const { return x_; }", "int get() const {\n      return x_;  // x\n    }"))
    assert same("a.jl", JULIA, JULIA.replace('"docstring"', '"other docstring"').replace("A grid.", "B grid."))
    assert not same("a.ts", TS, TS.replace("x * 2", "x * 3"))
    assert not same("a.cpp", CPP, CPP.replace("return x;\n}", "return -x;\n}"))   # inside #if branches


def test_code_only_lines_drop_comments_and_docstrings():
    units = adapter_for("a.jl").units(JULIA, True)
    fn = next(u for u in units if u.key.startswith("Grids.spacing(g :: Grid , i)"))
    assert fn.lines[0].text == "function spacing(g::Grid, i)"
    raw = next(u for u in adapter_for("a.jl").units(JULIA, False) if u.key == fn.key)
    assert raw.lines[0].text == '"docstring"'
    rs = adapter_for("a.rs").units(RUST, True)
    assert all("doc" not in l.text for u in rs for l in u.lines)


def test_unbalanced_source_falls_back():
    assert adapter_for("a.rs").units("fn f() {", True) is None
    assert adapter_for("a.jl").units("function f()\n", True) is None
