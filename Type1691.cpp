#include "pch.h"
#include <fstream>
#include <string>
#ifdef _DEBUG
#undef _DEBUG
#include <Python.h>
#define _DEBUG
#else
#include <Python.h>
#endif
#include "TRNSYS.h" //TRNSYS access functions (allow to acess TIME etc.) 

//---------------------------------------------------------------------------------------------------------------------- -
//Description: This subroutine calls a python script at every iteration. Wrappers have been written so that the Python script can have access to the
//             Parameters and Inputs and set the Outputs automatically.  Please see the documentation for a more complete description of how to use
//             this component.  Thanks to Yuichi Yasuda of Quattro Corporate Design for his help in creating this component.
//
//Last Modified :
//May 2017 - TPM & YY : original coding
//---------------------------------------------------------------------------------------------------------------------- -
//Copyright © 2017 Thermal Energy System Specialists, LLC, Madison, WI.All rights reserved.

//************************************************************************
// Wrapper functions for TRNSYS APIs
//************************************************************************

// A double precision function that returns the value of the parameter parNum.
static PyObject* TRNSYS_getParameterValue(PyObject *self, PyObject *args)
{
	int parNum = 0;
	double parVal;
	if (!PyArg_ParseTuple(args, "l", &parNum))
		return NULL;
	parVal = getParameterValue(&parNum);
	return PyFloat_FromDouble(parVal);
}

// A double precision function that returns the current value of the input inpNum.
static PyObject* TRNSYS_getInputValue(PyObject *self, PyObject *args)
{
	int inpNum = 0;
	double inpVal;
	if (!PyArg_ParseTuple(args, "l", &inpNum))
		return NULL;
	inpVal = getInputValue(&inpNum);
	return PyFloat_FromDouble(inpVal);
}

// A function to set the output value outNum in TRNSYS from the Python script.
static PyObject* TRNSYS_setOutputValue(PyObject *self, PyObject *args)
{
	int outNum = 0;
	double outVal = 0;
	if (!PyArg_ParseTuple(args, "ld", &outNum, &outVal))
		return NULL;

	setOutputValue(&outNum, &outVal);
	// return 
	Py_INCREF(Py_None);
	return Py_None;
}

static PyObject* TRNSYS_getTimeStep(PyObject* self, PyObject* args)
{
	return PyFloat_FromDouble(getSimulationTime());
}

static PyMethodDef EmbMethods[] = 
{
	{ "getParameterValue", TRNSYS_getParameterValue, METH_VARARGS, "A double precision function that returns the value of the current UnitÅfs ith parameter." },
	{ "getInputValue", TRNSYS_getInputValue, METH_VARARGS, "A double precision function that returns the current value of the current TypeÅfs ith input." },
	{ "setOutputValue", TRNSYS_setOutputValue, METH_VARARGS, "Send the value back to the TRNSYS kernel for global storage." },
	{ "getSimulationTime", TRNSYS_getTimeStep, METH_VARARGS, "Get simulation time" },
	{ NULL, NULL, 0, NULL }
};

static PyModuleDef EmbModule = 
{
	PyModuleDef_HEAD_INIT, "emb", NULL, -1, EmbMethods,
	NULL, NULL, NULL, NULL
};

static PyObject* PyInit_emb(void)
{
	return PyModule_Create(&EmbModule);
}


// A C++ routine for trimming the specified string
std::string trim(const std::string& str)
{
	size_t first = str.find_first_not_of(' ');
	if (std::string::npos == first)
	{
		return str;
	}
	size_t last = str.find_last_not_of(' ');
	return str.substr(first, (last - first + 1));
}

//Main body for the TRNSYS component
extern "C" __declspec(dllexport) void TYPE169(void)
{
	double Time, Timestep;
	int i, CurrentUnit, CurrentType, np, ni, no, index, errorCode;
	char type[20];
	char message[400];
	static std::string scriptName, RootDir, InputDir, scriptPath, functionName;
	std::string errorMessage;
	static PyObject *pName, *pModule, *pFunc, *pValue;

	//Get the Global Trnsys Simulation Variables
	Time = getSimulationTime();
	Timestep = getSimulationTimeStep();
	CurrentUnit = getCurrentUnit();
	CurrentType = getCurrentType();

	//Set the Version Number for This Type
	if (getIsVersionSigningTime())
	{
		int v = 17;
		setTypeVersion(&v);
		return;
	}

	//Do All of the Last Call Manipulations Here
	if (getIsLastCallofSimulation())
	{
		return;
	}

	//Perform Any "End of Timestep" Manipulations That May Be Required
	if (getIsEndOfTimestep()) 
	{
		return;
	}

	//Do All of the "Very First Call of the Simulation Manipulations" Here
	if (getIsFirstCallofSimulation())
	{
		//Tell the TRNSYS Engine How This Type Works
		np = 2;
		setNumberofParameters(&np);
		index = 1;
		ni = (int)(getParameterValue(&index) + 0.1);
		setNumberofInputs(&ni);
		int nder = 0;
		setNumberofDerivatives(&nder);
		index = 2;
		no = (int)(getParameterValue(&index) + 0.1);
		setNumberofOutputs(&no);
		int mode = 1;
		int staticStore = 0;
		int dynamicStore = 0;
		setIterationMode(&mode);
		setNumberStoredVariables(&staticStore, &dynamicStore);

		char unit[4];
		strcpy_s(unit, "DM1");
		i = 0;
		do
		{
			i++;
			setInputUnits(&i, unit, 3);
		} while (i < ni);
		i = 0;
		do
		{
			i++;
			setOutputUnits(&i, unit, 3);
		} while (i < no);

		return;
	}

	//Do All of the "Start Time" Manipulations Here - There Are No Iterations at the Intial Time
	if (getIsStartTime())
	{

		//Read in the Values of the Parameters from the Input File
		index = 1;
		ni = (int)(getParameterValue(&index) + 0.1);
		index = 2;
		no = (int)(getParameterValue(&index) + 0.1);

		//Check the Parameters for Problems
		if (ni < 0)
		{
			index = 2;
			strcpy_s(type, "FATAL");
			strcpy_s(message, "The number of inputs cannot be less than 0.");
			foundBadParameter(&index, type, message, (size_t)strlen(type), (size_t)strlen(message));
		}
		if (no < 0)
		{
			index = 3;
			strcpy_s(type, "FATAL");
			strcpy_s(message, "The number of outputs cannot be less than 0.");
			foundBadParameter(&index, type, message, (size_t)strlen(type), (size_t)strlen(message));
		}
			//Set the Initial Values of the Outputs
		i = 0;
		double val = 0.0;
		do
		{
			i++;
			setOutputValue(&i, &val);
		} while (i < no);
			
		//Get the Python script path, name and function name from the Labels
		index = 1;
		size_t maxlen = getMaxLabelLength();
		char *fname = new char[maxlen];
		char *str2 = getLabel(fname, maxlen, &CurrentUnit, &index);
		scriptName = trim(std::string(fname, 0, maxlen));
		index = 2;
		str2 = getLabel(fname, maxlen, &CurrentUnit, &index);
		functionName = trim(std::string(fname, 0, maxlen));
		delete[] fname;

		size_t maxpath = getMaxPathLength();
		char *dname = new char[maxpath];
		str2 = getTrnsysRootDir(dname, maxpath);
		RootDir = trim(std::string(dname, 0, maxpath));
		delete[] dname;

		char *iname = new char[maxpath];
		str2 = getTrnsysInputFileDir(iname, maxpath);
		InputDir = trim(std::string(iname, 0, maxpath));
		delete[] iname;

		//Determine the script file name with complete path
		char letterOne;
		char letterTwo;
		letterOne = scriptName[0];
		letterTwo = scriptName[1];
		if ((letterOne == '.') || (letterTwo == '\\'))
		{
			//Handle "Studio-like" relative paths : ".\" means "where TRNSYS is installed"
			scriptPath = RootDir + '\\' + scriptName.substr(2);
		}
		else if ((letterTwo /= ':') && (letterOne /= '\\'))
		{
			//	!Otherwise handle paths relative to the deck file
			scriptPath = InputDir +'\\' + scriptName;
		}
		else
		{
			//The full path is specified
			scriptPath = scriptName;
		}

		// Make sure that the script file exists in the location specified
		if (!std::ifstream(scriptPath))
		{
			errorCode = -1;
			strcpy_s(type, "Fatal");
			errorMessage = "The specified Python script file does not exist at the specified location: " + scriptPath;
			strcpy_s(message, errorMessage.c_str());
			messages(&errorCode, message, type, &CurrentUnit, &CurrentType, (size_t)strlen(message), (size_t)strlen(type));
			return;
		}

		//Strip the full path into the directory path and the script file name
		size_t last = scriptPath.find_last_of('\\');
		scriptName = scriptPath.substr(last+1);
		scriptPath = scriptPath.substr(0, last);
		//Create the string for adding the path to the Python serach path
		scriptPath = "sys.path.append(r'" + scriptPath + "')";
		//Remove the .py from the end of the script file name
		int dot = (int)scriptName.rfind(".");
		if (dot >= 0) scriptName.erase(dot, scriptName.length() - 1);

		// Import TRNSYS module into the Python environment
		PyImport_AppendInittab("TRNSYSpy", &PyInit_emb);

		Py_Initialize();
		// Add the script path
		PyRun_SimpleString("import sys");
		PyRun_SimpleString(scriptPath.c_str());

		pName = PyUnicode_DecodeFSDefault(scriptName.c_str());

		/* Error checking of pName left out */
		pModule = PyImport_Import(pName);

		if (pModule == NULL)
		{
			PyErr_Print();
			errorCode = -1;
			strcpy_s(type, "Fatal");
			strcpy_s(message, "The Python script file failed to load.");
			messages(&errorCode, message, type, &CurrentUnit, &CurrentType, (size_t)strlen(message), (size_t)strlen(type));
			return;
		}

		pFunc = PyObject_GetAttrString(pModule, functionName.c_str());

		if (!pFunc || !PyCallable_Check(pFunc))
		{
			if (PyErr_Occurred()) PyErr_Print();
			errorCode = -1;
			strcpy_s(type, "Fatal");
			errorMessage = "Failed to load the function, " + functionName + ", from the Python script file.";
			strcpy_s(message, errorMessage.c_str());
			messages(&errorCode, message, type, &CurrentUnit, &CurrentType, (size_t)strlen(message), (size_t)strlen(type));
			return;
		}

		Py_DECREF(pName);

		errorCode = -1;
		strcpy_s(type, "Notice");
		errorMessage = "The function, " + functionName + ", was loaded from the Python script file: " + scriptName;
		strcpy_s(message, errorMessage.c_str());
		messages(&errorCode, message, type, &CurrentUnit, &CurrentType, (size_t)strlen(message), (size_t)strlen(type));

		return;

	}

	//---------------------------------------------------------------------------------------------------------------------- -
	//ReRead the Parameters if Another Unit of This Type Has Been Called Last
	index = 1;
	ni = (int)(getParameterValue(&index) + 0.1);
	index = 2;
	no = (int)(getParameterValue(&index) + 0.1);
	//---------------------------------------------------------------------------------------------------------------------- -

	//---------------------------------------------------------------------------------------------------------------------- -
	//Perform All of the Calculations Here
	// Calling the function in the Python script - the Python script should get the inputs and parameters and assign the outputs directly using the Python extension functions created earlier
	try
	{
		pValue = PyObject_CallObject(pFunc, NULL);
		if (pValue != NULL) 
		{
			//Function called successfully
			Py_DECREF(pValue);
		}
		else
		{
			// Failed to calling the function
			PyErr_PrintEx(0);
			errorCode = -1;
			strcpy_s(type, "Fatal");
			errorMessage = "Failed to call the function, " + functionName + ", from the Python script file: " + scriptName;
			strcpy_s(message, errorMessage.c_str());
			messages(&errorCode, message, type, &CurrentUnit, &CurrentType, (size_t)strlen(message), (size_t)strlen(type));
			return;
		}
	}
	catch (...)
	{
		//An exception happened try to exit gracefully
		PyErr_PrintEx(0);
		errorCode = -1;
		strcpy_s(type, "Fatal");
		errorMessage = "Failed to call the function, " + functionName + ", from the Python script file: " + scriptName;
		strcpy_s(message, errorMessage.c_str());
		messages(&errorCode, message, type, &CurrentUnit, &CurrentType, (size_t)strlen(message), (size_t)strlen(type));
		return;
	}

//---------------------------------------------------------------------------------------------------------------------- -

	return;
}