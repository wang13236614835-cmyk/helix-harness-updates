using System;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Runtime.InteropServices;
using System.Windows.Forms;

sealed class HelixSplash : Control {
    [DllImport("user32.dll")]
    static extern bool SystemParametersInfo(uint action, uint parameter, out bool enabled, uint flags);

    readonly Timer frames = new Timer();
    readonly Stopwatch clock = new Stopwatch();
    readonly Font titleFont = new Font("Microsoft YaHei UI", 26, FontStyle.Bold);
    readonly Font statusFont = new Font("Microsoft YaHei UI", 11);
    readonly Font captionFont = new Font("Segoe UI", 9);
    readonly bool animate;
    bool finishing;
    double finishedAt;
    public event EventHandler Finished;

    public HelixSplash() {
        SetStyle(ControlStyles.UserPaint | ControlStyles.AllPaintingInWmPaint |
                 ControlStyles.OptimizedDoubleBuffer | ControlStyles.ResizeRedraw, true);
        BackColor = Color.FromArgb(246, 249, 249);
        ForeColor = Color.FromArgb(19, 75, 65);
        AccessibleRole = AccessibleRole.StaticText;
        AccessibleName = "Helix 工作台启动";
        bool enabled;
        animate = !SystemParametersInfo(0x1042, 0, out enabled, 0) || enabled;
        frames.Interval = 33;
        frames.Tick += delegate {
            if (finishing && (!animate || clock.Elapsed.TotalSeconds - finishedAt >= 0.24)) {
                frames.Stop();
                var done = Finished;
                if (done != null) done(this, EventArgs.Empty);
                return;
            }
            Invalidate();
        };
    }

    protected override void OnVisibleChanged(EventArgs e) {
        base.OnVisibleChanged(e);
        if (Visible) {
            finishing = false;
            clock.Restart();
            if (animate) frames.Start();
        } else frames.Stop();
    }

    protected override void OnTextChanged(EventArgs e) {
        base.OnTextChanged(e);
        AccessibleDescription = Text;
        Invalidate();
    }

    public void Complete() {
        if (finishing) return;
        finishing = true;
        finishedAt = clock.Elapsed.TotalSeconds;
        frames.Start();
    }

    protected override void OnPaint(PaintEventArgs e) {
        base.OnPaint(e);
        Graphics g = e.Graphics;
        g.SmoothingMode = SmoothingMode.AntiAlias;
        g.TextRenderingHint = System.Drawing.Text.TextRenderingHint.ClearTypeGridFit;
        float scale = Math.Min(1.35f, Math.Max(0.7f, Math.Min(Width / 1000f, Height / 720f)));
        float cx = Width / 2f;
        float cy = Height / 2f - 56 * scale;
        double phase = animate ? clock.Elapsed.TotalSeconds * 1.3 : 0.8;
        float reveal = animate ? Math.Min(1f, (float)clock.Elapsed.TotalSeconds / 0.55f) : 1f;
        float opacity = finishing ? Math.Max(0f, 1f - (float)(clock.Elapsed.TotalSeconds - finishedAt) / 0.24f) : 1f;
        using (var halo = new SolidBrush(Color.FromArgb(18, 44, 165, 132)))
            g.FillEllipse(halo, cx - 144 * scale, cy - 132 * scale, 288 * scale, 264 * scale);
        PointF[] left = new PointF[65], right = new PointF[65];
        for (int i = 0; i < left.Length; i++) {
            double position = i / 64.0;
            double angle = position * Math.PI * 3.8 + phase;
            float x = (float)Math.Sin(angle) * 48 * scale;
            float y = cy - 100 * scale + (float)position * 200 * scale;
            left[i] = new PointF(cx + x, y);
            right[i] = new PointF(cx - x, y);
            if (i % 4 == 0 && position <= reveal) {
                int alpha = (int)((55 + 65 * (Math.Cos(angle) + 1) / 2) * opacity);
                using (var rung = new Pen(Color.FromArgb(alpha, 33, 125, 100), 2 * scale))
                    g.DrawLine(rung, left[i], right[i]);
            }
        }
        int count = Math.Max(2, (int)(64 * reveal) + 1);
        for (int i = 1; i < count; i++) {
            double depth = (Math.Cos(i / 64.0 * Math.PI * 3.8 + phase) + 1) / 2;
            using (var a = new Pen(Color.FromArgb((int)((120 + 130 * depth) * opacity), 18, 105, 83), 4 * scale))
            using (var b = new Pen(Color.FromArgb((int)((250 - 130 * depth) * opacity), 65, 177, 147), 4 * scale)) {
                a.StartCap = a.EndCap = b.StartCap = b.EndCap = LineCap.Round;
                g.DrawLine(a, left[i - 1], left[i]);
                g.DrawLine(b, right[i - 1], right[i]);
            }
        }
        using (var align = new StringFormat { Alignment = StringAlignment.Center, LineAlignment = StringAlignment.Center })
        using (var ink = new SolidBrush(Color.FromArgb((int)(255 * opacity), ForeColor)))
        using (var muted = new SolidBrush(Color.FromArgb((int)(255 * opacity), 88, 119, 110))) {
            g.DrawString("Helix", titleFont, ink, new RectangleF(0, cy + 123 * scale, Width, 56), align);
            g.DrawString(Text, statusFont, muted, new RectangleF(40, cy + 182 * scale, Width - 80, 92), align);
            g.DrawString("工作台 · 研究与协作", captionFont, muted, new RectangleF(0, Height - 72, Width, 30), align);
        }
    }

    protected override void Dispose(bool disposing) {
        if (disposing) {
            frames.Stop(); frames.Dispose();
            titleFont.Dispose(); statusFont.Dispose(); captionFont.Dispose();
        }
        base.Dispose(disposing);
    }
}
