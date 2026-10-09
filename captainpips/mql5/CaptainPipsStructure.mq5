//+------------------------------------------------------------------+
//| CaptainPipsStructure.mq5                                         |
//| Structure visualization indicator for CaptainPips V2              |
//| Reads GlobalVariables set by EA and draws legs/stops/entries      |
//+------------------------------------------------------------------+
#property copyright "CaptainPips V2"
#property version   "2.0"
#property strict
#property indicator_chart_window

//+------------------------------------------------------------------+
//| Input parameters                                                 |
//+------------------------------------------------------------------+
input color InpBullColor     = clrGreen;    // Bull leg color
input color InpBearColor     = clrRed;      // Bear leg color
input color InpStopColor     = clrGray;     // Stop color
input bool  InpFillBull      = true;        // Fill bull rectangles
input bool  InpFillBear      = true;        // Fill bear rectangles
input bool  InpFillStop      = false;       // Fill stop rectangles
input color InpEntryColor    = clrYellow;   // Entry signal color
input bool  InpLabelAtStart  = false;       // Label at start (true) or end (false) of rectangle
input double InpLabelOffset  = 0.3;         // Label offset as % of rectangle height (0.0-1.0)
input int   InpLabelFontSize = 8;           // Label font size
input ENUM_ANCHOR_POINT InpLabelAnchor = ANCHOR_LEFT_LOWER; // Label anchor point
input int   InpLabelOffsetBars = 2;         // Horizontal offset in bars

//+------------------------------------------------------------------+
//| Global state                                                     |
//+------------------------------------------------------------------+
int g_leg_count  = 0;
int g_stop_count = 0;
bool g_entry_active    = false;
bool g_failzone_active = false;

//+------------------------------------------------------------------+
//| Initialization                                                   |
//+------------------------------------------------------------------+
int OnInit()
{
    return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| Deinitialization                                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
    DeleteAllObjects();
}

//+------------------------------------------------------------------+
//| OnCalculate — fires every tick                                   |
//+------------------------------------------------------------------+
int OnCalculate(const int rates_total,
                const int prev_calculated,
                const datetime &time[],
                const double &open[],
                const double &high[],
                const double &low[],
                const double &close[],
                const long &tick_volume[],
                const long &volume[],
                const int &spread[])
{
    // Check for reset signal
    if (GlobalVariableCheck("CP_RESET"))
    {
        double reset_val = GlobalVariableGet("CP_RESET");
        if (reset_val > 0)
        {
            DeleteAllObjects();
            GlobalVariableSet("CP_RESET", 0);
            GlobalVariableSet("CP_FAILZONE_active", 0);
            g_leg_count     = 0;
            g_stop_count    = 0;
            g_entry_active  = false;
            g_failzone_active = false;
            return rates_total;
        }
    }

    // Read current counts
    int leg_count  = (int)GVGet("CP_COUNT_LEG", 0);
    int stop_count = (int)GVGet("CP_COUNT_STOP", 0);

    // --- Legs ---
    for (int i = 1; i <= leg_count; i++)
        UpdateLegRectangle(i);
    for (int i = leg_count + 1; i <= g_leg_count; i++)
    {
        DeleteObject(StringFormat("CP_LEG_%d", i));
        DeleteObject(StringFormat("CP_LEG_%d_lbl", i));
    }
    g_leg_count = leg_count;

    // --- Stops ---
    for (int i = 1; i <= stop_count; i++)
        UpdateStopRectangle(i);
    for (int i = stop_count + 1; i <= g_stop_count; i++)
    {
        DeleteObject(StringFormat("CP_STOP_%d", i));
        DeleteObject(StringFormat("CP_STOP_%d_lbl", i));
    }
    g_stop_count = stop_count;

    // --- Entry signal ---
    UpdateEntryRectangle();

    // --- Fail zone ---
    UpdateFailZone();

    ChartRedraw(ChartID());
    return rates_total;
}

//+------------------------------------------------------------------+
//| Update leg rectangle                                             |
//+------------------------------------------------------------------+
void UpdateLegRectangle(int leg_num)
{
    string prefix = StringFormat("CP_LEG_%d", leg_num);
    string name   = prefix;

    double t1  = GVGet(prefix + "_t1", 0);
    double t2  = GVGet(prefix + "_t2", 0);
    double p1  = GVGet(prefix + "_p1", 0);
    double p2  = GVGet(prefix + "_p2", 0);
    double dir = GVGet(prefix + "_dir", 0);

    if (t1 == 0 || t2 == 0 || p1 == 0 || p2 == 0)
        return;

    bool is_bull = (dir > 0);
    color clr    = is_bull ? InpBullColor : InpBearColor;
    bool  fill   = is_bull ? InpFillBull  : InpFillBear;

    string label = StringFormat("L%d", leg_num);

    CreateOrUpdateRect(name,
                       (datetime)(int)t1, p1,
                       (datetime)(int)t2, p2,
                       clr, fill, 2, STYLE_SOLID);
    DrawLabel(name, (datetime)(int)t1, (datetime)(int)t2, p1, p2, label, clr);
}

//+------------------------------------------------------------------+
//| Update stop rectangle                                            |
//+------------------------------------------------------------------+
void UpdateStopRectangle(int stop_num)
{
    string prefix = StringFormat("CP_STOP_%d", stop_num);
    string name   = prefix;

    double t1 = GVGet(prefix + "_t1", 0);
    double t2 = GVGet(prefix + "_t2", 0);
    double p1 = GVGet(prefix + "_p1", 0);
    double p2 = GVGet(prefix + "_p2", 0);

    if (t1 == 0 || t2 == 0 || p1 == 0 || p2 == 0)
        return;

    string label = StringFormat("S%d", stop_num);

    CreateOrUpdateRect(name,
                       (datetime)(int)t1, p1,
                       (datetime)(int)t2, p2,
                       InpStopColor, InpFillStop, 1, STYLE_DOT);
    DrawLabel(name, (datetime)(int)t1, (datetime)(int)t2, p1, p2, label, InpStopColor);
}

//+------------------------------------------------------------------+
//| Update fail zone — thin orange horizontal line                   |
//+------------------------------------------------------------------+
void UpdateFailZone()
{
    string name = "CP_FAILZONE";

    double active = GVGet("CP_FAILZONE_active", 0);

    if (active < 1)
    {
        if (g_failzone_active)
        {
            DeleteObject(name);
            DeleteObject(name + "_lbl");
            g_failzone_active = false;
        }
        return;
    }

    double t1 = GVGet("CP_FAILZONE_t1", 0);
    double t2 = GVGet("CP_FAILZONE_t2", 0);
    double p1 = GVGet("CP_FAILZONE_p1", 0);
    double p2 = GVGet("CP_FAILZONE_p2", 0);

    if (t1 == 0 || p1 == 0 || p2 == 0)
        return;

    // Default t2 to 2 hours after t1 if not set
    if (t2 == 0) t2 = t1 + 7200;

    // Draw as thin orange rectangle
    CreateOrUpdateRect(name,
                       (datetime)(int)t1, p1,
                       (datetime)(int)t2, p2,
                       clrOrange, false, 1, STYLE_SOLID);

    // Label at midpoint
    double mid_price = (p1 + p2) / 2.0;
    string lname = name + "_lbl";
    long chart = ChartID();
    if (ObjectFind(chart, lname) < 0)
        ObjectCreate(chart, lname, OBJ_TEXT, 0, (datetime)(int)t2, mid_price);
    else
    {
        ObjectSetInteger(chart, lname, OBJPROP_TIME, 0, (datetime)(int)t2);
        ObjectSetDouble(chart, lname, OBJPROP_PRICE, 0, mid_price);
    }
    ObjectSetString(chart, lname, OBJPROP_TEXT, "FailZone");
    ObjectSetInteger(chart, lname, OBJPROP_COLOR, clrOrange);
    ObjectSetInteger(chart, lname, OBJPROP_FONTSIZE, InpLabelFontSize);
    ObjectSetString(chart, lname, OBJPROP_FONT, "Arial Bold");
    ObjectSetInteger(chart, lname, OBJPROP_ANCHOR, ANCHOR_LEFT_LOWER);
    ObjectSetInteger(chart, lname, OBJPROP_SELECTABLE, false);
    ObjectSetInteger(chart, lname, OBJPROP_BACK, false);

    g_failzone_active = true;
}

//+------------------------------------------------------------------+
//| Update entry signal rectangle                                    |
//+------------------------------------------------------------------+
void UpdateEntryRectangle()
{
    string name = "CP_ENTRY";

    double active = GVGet("CP_ENTRY_active", 0);

    if (active < 1)
    {
        if (g_entry_active)
        {
            DeleteObject(name);
            DeleteObject(name + "_lbl");
            g_entry_active = false;
        }
        return;
    }

    double t1       = GVGet("CP_ENTRY_t1", 0);
    double t2       = GVGet("CP_ENTRY_t2", 0);
    double price    = GVGet("CP_ENTRY_price", 0);
    double sl       = GVGet("CP_ENTRY_sl", 0);
    double strategy = GVGet("CP_ENTRY_strategy", 0);

    if (t1 == 0 || price == 0 || sl == 0)
        return;

    // Default t2 to t1 + 60 if not set
    if (t2 == 0) t2 = t1 + 60;

    // Strategy label
    string strat_label = "Entry";
    if ((int)strategy == 1) strat_label = "Leg2Chain";
    if ((int)strategy == 2) strat_label = "Leg3Drive";

    // Rectangle from entry price to SL
    CreateOrUpdateRect(name,
                       (datetime)(int)t1, price,
                       (datetime)(int)t2, sl,
                       InpEntryColor, true, 2, STYLE_SOLID);
    DrawLabel(name, (datetime)(int)t1, (datetime)(int)t2, price, sl, strat_label, InpEntryColor);

    g_entry_active = true;
}

//+------------------------------------------------------------------+
//| Create or update a rectangle                                     |
//+------------------------------------------------------------------+
void CreateOrUpdateRect(string name,
                        datetime t1, double p1,
                        datetime t2, double p2,
                        color clr, bool fill,
                        int width, ENUM_LINE_STYLE style)
{
    long chart = ChartID();

    if (ObjectFind(chart, name) < 0)
        ObjectCreate(chart, name, OBJ_RECTANGLE, 0, t1, p1, t2, p2);
    else
    {
        ObjectSetInteger(chart, name, OBJPROP_TIME,  0, t1);
        ObjectSetDouble(chart, name, OBJPROP_PRICE, 0, p1);
        ObjectSetInteger(chart, name, OBJPROP_TIME,  1, t2);
        ObjectSetDouble(chart, name, OBJPROP_PRICE, 1, p2);
    }

    ObjectSetInteger(chart, name, OBJPROP_COLOR, clr);
    ObjectSetInteger(chart, name, OBJPROP_STYLE, style);
    ObjectSetInteger(chart, name, OBJPROP_WIDTH, width);
    ObjectSetInteger(chart, name, OBJPROP_FILL, fill);
    ObjectSetInteger(chart, name, OBJPROP_BACK, true);
    ObjectSetInteger(chart, name, OBJPROP_SELECTABLE, false);
}

//+------------------------------------------------------------------+
//| Draw a text label on/above a rectangle                           |
//+------------------------------------------------------------------+
void DrawLabel(string rect_name, datetime t1, datetime t2, double p1, double p2,
               string text, color clr)
{
    string lname = rect_name + "_lbl";
    long chart = ChartID();

    // Calculate position
    double topPrice = MathMax(p1, p2);
    double height = MathAbs(p1 - p2);
    double labelPrice = topPrice + (height * InpLabelOffset);

    // Time position: start (t1) or end (t2) of rectangle
    datetime labelTime = InpLabelAtStart ? t1 : t2;
    // Add horizontal offset in bars
    int periodSec = PeriodSeconds(PERIOD_CURRENT);
    labelTime = labelTime + (InpLabelOffsetBars * periodSec);

    if (ObjectFind(chart, lname) < 0)
        ObjectCreate(chart, lname, OBJ_TEXT, 0, labelTime, labelPrice);
    else
    {
        ObjectSetInteger(chart, lname, OBJPROP_TIME, 0, labelTime);
        ObjectSetDouble(chart, lname, OBJPROP_PRICE, 0, labelPrice);
    }

    ObjectSetString(chart, lname, OBJPROP_TEXT, text);
    ObjectSetInteger(chart, lname, OBJPROP_COLOR, clr);
    ObjectSetInteger(chart, lname, OBJPROP_FONTSIZE, InpLabelFontSize);
    ObjectSetString(chart, lname, OBJPROP_FONT, "Arial Bold");
    ObjectSetInteger(chart, lname, OBJPROP_ANCHOR, InpLabelAnchor);
    ObjectSetInteger(chart, lname, OBJPROP_SELECTABLE, false);
    ObjectSetInteger(chart, lname, OBJPROP_BACK, false);
}

//+------------------------------------------------------------------+
//| Delete a single chart object                                     |
//+------------------------------------------------------------------+
void DeleteObject(string name)
{
    if (ObjectFind(ChartID(), name) >= 0)
        ObjectDelete(ChartID(), name);
}

//+------------------------------------------------------------------+
//| Delete all CP_ objects from chart                                |
//+------------------------------------------------------------------+
void DeleteAllObjects()
{
    int total = ObjectsTotal(ChartID(), 0, -1);
    for (int i = total - 1; i >= 0; i--)
    {
        string name = ObjectName(ChartID(), i, 0, -1);
        if (StringFind(name, "CP_") == 0)
            ObjectDelete(ChartID(), name);
    }
}

//+------------------------------------------------------------------+
//| Safe GlobalVariable getter with default                          |
//+------------------------------------------------------------------+
double GVGet(const string name, double default_val)
{
    if (!GlobalVariableCheck(name))
        return default_val;
    return GlobalVariableGet(name);
}
