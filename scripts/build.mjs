import { runPython } from './python.mjs';

// Streamlit runs Python directly; validate syntax and the application's test suite.
runPython(['-m', 'compileall', '-q', 'app.py', 'antibody_manager']);
runPython(['-m', 'pytest', '-q', '-p', 'no:cacheprovider']);
console.log('Build passed: Python compilation and regression tests.');
