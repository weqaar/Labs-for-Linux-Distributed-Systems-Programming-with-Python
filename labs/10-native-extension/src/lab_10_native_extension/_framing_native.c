#define PY_SSIZE_T_CLEAN
#include <Python.h>

static PyObject *relay_fletcher16(PyObject *self, PyObject *arg)
{
    Py_buffer view;
    unsigned int sum1 = 0;
    unsigned int sum2 = 0;
    Py_ssize_t index;
    const unsigned char *data;
    (void)self;

    if (PyObject_GetBuffer(arg, &view, PyBUF_SIMPLE) != 0) {
        return NULL;
    }
    data = (const unsigned char *)view.buf;
    for (index = 0; index < view.len; index++) {
        sum1 = (sum1 + data[index]) % 255u;
        sum2 = (sum2 + sum1) % 255u;
    }
    PyBuffer_Release(&view);
    return PyLong_FromUnsignedLong((sum2 << 8) | sum1);
}

static PyObject *relay_parse_length_prefix(PyObject *self, PyObject *args, PyObject *kwargs)
{
    static char *keywords[] = {"frame", "maximum_frame_size", NULL};
    PyObject *frame_obj;
    Py_buffer view;
    unsigned int maximum_frame_size = 1024u * 1024u;
    unsigned int length;
    const unsigned char *data;
    (void)self;

    if (!PyArg_ParseTupleAndKeywords(
            args,
            kwargs,
            "O|I:parse_length_prefix",
            keywords,
            &frame_obj,
            &maximum_frame_size)) {
        return NULL;
    }
    if (PyObject_GetBuffer(frame_obj, &view, PyBUF_SIMPLE) != 0) {
        return NULL;
    }
    if (view.len < 4) {
        PyBuffer_Release(&view);
        PyErr_SetString(PyExc_ValueError, "frame header requires 4 bytes");
        return NULL;
    }
    data = (const unsigned char *)view.buf;
    length = ((unsigned int)data[0] << 24)
        | ((unsigned int)data[1] << 16)
        | ((unsigned int)data[2] << 8)
        | (unsigned int)data[3];
    PyBuffer_Release(&view);
    if (length > maximum_frame_size) {
        PyErr_Format(
            PyExc_ValueError,
            "frame exceeds maximum size of %u bytes",
            maximum_frame_size);
        return NULL;
    }
    return PyLong_FromUnsignedLong(length);
}

static PyMethodDef relay_methods[] = {
    {"fletcher16", (PyCFunction)relay_fletcher16, METH_O, PyDoc_STR("Compute a Fletcher-16 checksum.")},
    {"parse_length_prefix", (PyCFunction)(void(*)(void))relay_parse_length_prefix, METH_VARARGS | METH_KEYWORDS, PyDoc_STR("Parse a 4-byte big-endian length prefix.")},
    {NULL, NULL, 0, NULL}
};

static struct PyModuleDef relay_module = {
    PyModuleDef_HEAD_INIT,
    "_framing_native",
    "Native helpers for relay framing.",
    -1,
    relay_methods,
};

PyMODINIT_FUNC PyInit__framing_native(void)
{
    return PyModule_Create(&relay_module);
}
