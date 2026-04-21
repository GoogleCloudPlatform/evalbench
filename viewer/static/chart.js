function drawChart() {
    const data = window.chartData;
    const config = window.chartConfig;
    
    const xCol = config.xCol;
    const yCol = config.yCol;
    const hueCol = config.hueCol;
    const title = config.title;
    const ylabel = config.ylabel;

    const margin = { top: 60, right: 350, bottom: 60, left: 60 };
    const container = document.getElementById('chart-container');
    if (!container) return;

    const width = container.clientWidth - margin.left - margin.right;
    const height = 500 - margin.top - margin.bottom;

    // Clear previous SVG
    d3.select('#chart').selectAll("*").remove();

    // Sort data by time
    data.sort((a, b) => new Date(a[xCol]) - new Date(b[xCol]));
    data.forEach((d, i) => d.index = i);

    const svg = d3.select('#chart')
        .append("svg")
        .attr("width", width + margin.left + margin.right)
        .attr("height", height + margin.top + margin.bottom)
        .append("g")
        .attr("transform", `translate(${margin.left},${margin.top})`);

    // X axis - Use linear scale with indices to support zooming while maintaining even spacing
    const x = d3.scaleLinear()
        .domain([0, data.length - 1])
        .range([0, width]);

    // Y axis
    const y = d3.scaleLinear()
        .domain([0, d3.max(data, d => d[yCol]) * 1.1])
        .range([height, 0]);

    // Grid lines
    svg.append("g")
        .attr("class", "grid")
        .attr("transform", `translate(0,${height})`)
        .call(d3.axisBottom(x).tickSize(-height).tickFormat(""));

    svg.append("g")
        .attr("class", "grid")
        .call(d3.axisLeft(y).tickSize(-width).tickFormat(""));

    // X Axis Label formatter
    const xAxis = d3.axisBottom(x)
        .tickFormat(i => {
            const idx = Math.round(i);
            if (Math.abs(i - idx) < 0.1 && data[idx]) {
                return data[idx][xCol];
            }
            return "";
        });

    const gX = svg.append("g")
        .attr("transform", `translate(0,${height})`)
        .call(xAxis);
        
    gX.selectAll("text")
        .attr("transform", "rotate(-45)")
        .style("text-anchor", "end")
        .attr("class", "axis-label");

    svg.append("g")
        .call(d3.axisLeft(y))
        .selectAll("text")
        .attr("class", "axis-label");

    // Color scale - Premium palette
    const products = [...new Set(data.map(d => d[hueCol]))];
    const colors = ['#6366f1', '#10b981', '#f43f5e', '#f59e0b', '#8b5cf6'];
    const color = d3.scaleOrdinal()
        .domain(products)
        .range(colors);

    // Group data by product
    const dataByProduct = d3.group(data, d => d[hueCol]);

    // Clip path
    svg.append("defs").append("clipPath")
        .attr("id", "clip")
        .append("rect")
        .attr("width", width)
        .attr("height", height);

    // Chart body
    const chartBody = svg.append("g")
        .attr("clip-path", "url(#clip)");

    // Zoom
    const zoom = d3.zoom()
        .scaleExtent([1, 20])
        .translateExtent([[0, 0], [width, height]])
        .extent([[0, 0], [width, height]])
        .on("zoom", zoomed);

    // Zoom rect
    chartBody.append("rect")
        .attr("width", width)
        .attr("height", height)
        .style("fill", "none")
        .style("pointer-events", "all")
        .call(zoom);

    // Draw smooth lines and areas
    dataByProduct.forEach((productData, product) => {
        const productIndex = products.indexOf(product);
        // Area
        chartBody.append("path")
            .datum(productData)
            .attr("class", `area product-${productIndex}`)
            .attr("d", d3.area()
                .x(d => x(d.index))
                .y0(height)
                .y1(d => y(d[yCol]))
            )
            .style("fill", color(product))
            .style("opacity", 0.1)
            .style("pointer-events", "none");

        // Line
        chartBody.append("path")
            .datum(productData)
            .attr("class", `line product-${productIndex}`)
            .attr("d", d3.line()
                .x(d => x(d.index))
                .y(d => y(d[yCol]))
            )
            .style("stroke", color(product));
    });

    // Add dots and tooltips
    const tooltip = d3.select("#tooltip");

    chartBody.selectAll(".dot")
        .data(data)
        .enter()
        .append("circle")
        .attr("cx", d => x(d.index))
        .attr("cy", d => y(d[yCol]))
        .attr("r", 5)
        .attr("fill", d => color(d[hueCol]))
        .attr("class", d => `dot product-${products.indexOf(d[hueCol])}`)
        .on("mouseover", function (event, d) {
            d3.select(this).attr("r", 8).style("stroke-width", "3px");
            tooltip.style("opacity", 1)
                .html(`<strong>Product:</strong> ${d[hueCol]}<br/><strong>Time:</strong> ${d[xCol]}<br/><strong>Value:</strong> ${d[yCol]}<br/><strong>Eval ID:</strong> ${d.job_id}`);
        })
        .on("mousemove", function (event) {
            tooltip.style("left", (event.pageX + 15) + "px")
                .style("top", (event.pageY - 28) + "px");
        })
        .on("mouseout", function () {
            d3.select(this).attr("r", 5).style("stroke-width", "2px");
            tooltip.style("opacity", 0);
        });


    // Add Title
    svg.append("text")
        .attr("x", width / 2)
        .attr("y", -margin.top / 2)
        .attr("text-anchor", "middle")
        .attr("class", "chart-title")
        .text(title);

    // Add Y axis label
    svg.append("text")
        .attr("transform", "rotate(-90)")
        .attr("y", -margin.left + 20)
        .attr("x", -height / 2)
        .attr("text-anchor", "middle")
        .style("font-size", "12px")
        .style("fill", "#64748b")
        .style("font-weight", "600")
        .text(ylabel);

    // Add Legend
    const legend = svg.selectAll(".legend")
        .data(products)
        .enter().append("g")
        .attr("class", "legend")
        .attr("transform", (d, i) => `translate(${width + 20}, ${i * 25})`)
        .style("cursor", "pointer")
        .on("click", function(event, product) {
            const productIndex = products.indexOf(product);
            
            // Check if ANY OTHER line is visible
            let anyOtherVisible = false;
            products.forEach((p, i) => {
                if (i !== productIndex) {
                    const el = d3.selectAll(`.line.product-${i}`);
                    if (el.style("opacity") !== "0") {
                        anyOtherVisible = true;
                    }
                }
            });
            
            if (anyOtherVisible) {
                // ISOLATE
                products.forEach((p, i) => {
                    const newOpacity = (i === productIndex) ? 1 : 0;
                    const areaOpacity = (i === productIndex) ? 0.1 : 0;
                    
                    d3.selectAll(`.line.product-${i}, .dot.product-${i}`)
                        .transition().duration(200).style("opacity", newOpacity)
                        .style("pointer-events", newOpacity === 0 ? "none" : "all");
                        
                    d3.selectAll(`.area.product-${i}`)
                        .transition().duration(200).style("opacity", areaOpacity);
                        
                    // Update legend
                    const leg = d3.selectAll(".legend").filter(d => d === p);
                    leg.select("rect").style("opacity", newOpacity === 0 ? 0.3 : 1);
                    leg.select("text").style("opacity", newOpacity === 0 ? 0.5 : 1);
                });
            } else {
                // RESTORE
                products.forEach((p, i) => {
                    d3.selectAll(`.line.product-${i}, .dot.product-${i}`)
                        .transition().duration(200).style("opacity", 1)
                        .style("pointer-events", "all");
                        
                    d3.selectAll(`.area.product-${i}`)
                        .transition().duration(200).style("opacity", 0.1);
                        
                    // Update legend
                    const leg = d3.selectAll(".legend").filter(d => d === p);
                    leg.select("rect").style("opacity", 1);
                    leg.select("text").style("opacity", 1);
                });
            }
        });

    legend.append("rect")
        .attr("x", 0)
        .attr("width", 12)
        .attr("height", 12)
        .attr("rx", 3)
        .style("fill", color);

    legend.append("text")
        .attr("x", 20)
        .attr("y", 6)
        .attr("dy", ".35em")
        .style("text-anchor", "start")
        .text(d => d.replace('.json', ''));

    function zoomed(event) {
        const newX = event.transform.rescaleX(x);
        
        // Update axis
        gX.call(xAxis.scale(newX));
        gX.selectAll("text")
            .attr("transform", "rotate(-45)")
            .style("text-anchor", "end");
            
        // Update lines
        chartBody.selectAll(".line")
            .attr("d", function(d) {
                return d3.line()
                    .x(p => newX(p.index))
                    .y(p => y(p[yCol]))
                    (d);
            });
            
        // Update areas
        chartBody.selectAll(".area")
            .attr("d", function(d) {
                return d3.area()
                    .x(p => newX(p.index))
                    .y0(height)
                    .y1(p => y(p[yCol]))
                    (d);
            });
            
        // Update dots
        chartBody.selectAll(".dot")
            .attr("cx", d => newX(d.index));
    }
}

// Initial draw
drawChart();

// Redraw on resize
window.addEventListener('resize', drawChart);
